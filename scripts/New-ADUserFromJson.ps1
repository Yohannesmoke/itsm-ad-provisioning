<#
.SYNOPSIS
    Create an Active Directory user from a JSON record read on stdin.

.DESCRIPTION
    Called by the Python webhook (app/provisioner.py). Reading the record from
    stdin keeps the password off the command line, and every AD filter value is
    escaped, so ticket content cannot change the query.

    Always writes exactly one JSON line to stdout:
      {"success": bool, "message": str, "details": {...}}

.EXAMPLE
    Get-Content examples/user.json | pwsh -File scripts/New-ADUserFromJson.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Write-Result([bool]$Success, [string]$Message, $Details = @{}) {
    [ordered]@{ success = $Success; message = $Message; details = $Details } |
        ConvertTo-Json -Compress -Depth 5 | Write-Output
}

function Format-FilterValue([string]$Value) {
    # Single quotes are the only metacharacter inside a quoted AD filter string.
    return $Value -replace "'", "''"
}

try {
    $user = [Console]::In.ReadToEnd() | ConvertFrom-Json
    foreach ($required in 'first_name', 'last_name', 'username', 'password', 'ou') {
        if ([string]::IsNullOrWhiteSpace($user.$required)) { throw "Missing field '$required'" }
    }

    Import-Module ActiveDirectory

    $sam = Format-FilterValue $user.username
    if (Get-ADUser -Filter "SamAccountName -eq '$sam'") {
        Write-Result $false "User already exists in AD"
        exit 0
    }

    $details = [ordered]@{ ou = $user.ou }

    $managerDn = $null
    if ($user.manager) {
        $m = Format-FilterValue $user.manager
        $mgr = Get-ADUser -Filter "SamAccountName -eq '$m' -or DisplayName -eq '$m' -or Name -eq '$m'" |
            Select-Object -First 1
        if ($mgr) { $managerDn = $mgr.DistinguishedName; $details.manager = $mgr.SamAccountName }
        else { $details.manager = "not found: $($user.manager)" }
    }

    $displayName = if ($user.display_name) { $user.display_name } else { "$($user.first_name) $($user.last_name)" }
    $params = @{
        Name                  = $displayName
        GivenName             = $user.first_name
        Surname               = $user.last_name
        DisplayName           = $displayName
        SamAccountName        = $user.username
        UserPrincipalName     = "$($user.username)@$((Get-ADDomain).DNSRoot)"
        AccountPassword       = ConvertTo-SecureString $user.password -AsPlainText -Force
        ChangePasswordAtLogon = [bool]$user.change_password_at_logon
        Enabled               = $true
        Path                  = $user.ou
    }
    if ($user.description) { $params.Description = $user.description }
    if ($user.email)       { $params.EmailAddress = $user.email }
    if ($user.phone)       { $params.OfficePhone = $user.phone }
    if ($managerDn)        { $params.Manager = $managerDn }

    New-ADUser @params

    $groupResults = @()
    foreach ($groupName in @($user.groups)) {
        if ([string]::IsNullOrWhiteSpace($groupName) -or $groupName -eq 'Domain Users') { continue }
        $g = Format-FilterValue $groupName
        $group = Get-ADGroup -Filter "Name -eq '$g'"
        if ($group) {
            Add-ADGroupMember -Identity $group -Members $user.username
            $groupResults += @{ name = $groupName; status = 'added' }
        } else {
            $groupResults += @{ name = $groupName; status = 'not found' }
        }
    }
    $details.groups = $groupResults

    Write-Result $true "User created" $details
    exit 0
}
catch {
    $msg = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
    Write-Result $false $msg
    exit 1
}
