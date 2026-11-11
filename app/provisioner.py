"""Back ends that actually create the account.

PowerShellProvisioner runs scripts/New-ADUserFromJson.ps1 on a domain-joined
Windows host. The user record is passed as JSON on stdin, so passwords never
appear on a command line or in the process list, and there is no string
concatenation that could be abused for command injection.

DryRunProvisioner is used for local development, demos and CI.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .parser import UserRequest

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "New-ADUserFromJson.ps1"


@dataclass
class ProvisionResult:
    username: str
    success: bool
    message: str
    details: dict = field(default_factory=dict)


class Provisioner(Protocol):
    def create(self, user: UserRequest) -> ProvisionResult: ...


class DryRunProvisioner:
    """Pretends to create users. Remembers usernames to simulate 'already exists'."""

    def __init__(self) -> None:
        self.created: set[str] = set()

    def create(self, user: UserRequest) -> ProvisionResult:
        if user.username in self.created:
            return ProvisionResult(user.username, False, "User already exists (dry run)")
        self.created.add(user.username)
        return ProvisionResult(
            user.username,
            True,
            "User would be created (dry run)",
            {"ou": user.ou, "groups": [{"name": g, "status": "would add"} for g in user.groups]},
        )


class PowerShellProvisioner:
    def __init__(self, script: Path = SCRIPT, timeout: int = 60, executable: str | None = None) -> None:
        self.script = script
        self.timeout = timeout
        self.executable = executable or shutil.which("pwsh") or shutil.which("powershell.exe") or "powershell.exe"

    def create(self, user: UserRequest) -> ProvisionResult:
        cmd = [self.executable, "-NonInteractive", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.script)]
        try:
            proc = subprocess.run(
                cmd,
                input=json.dumps(user.to_dict()),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ProvisionResult(user.username, False, f"PowerShell timed out after {self.timeout}s")
        except OSError as exc:
            return ProvisionResult(user.username, False, f"Could not start PowerShell: {exc}")

        result = _last_json_line(proc.stdout)
        if result is None:
            logger.error("PowerShell returned no JSON (exit %s): %s", proc.returncode, proc.stderr.strip())
            return ProvisionResult(user.username, False, proc.stderr.strip() or "PowerShell returned no result")
        return ProvisionResult(
            user.username,
            bool(result.get("success")),
            str(result.get("message", "")),
            result.get("details") or {},
        )


def _last_json_line(stdout: str) -> dict | None:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def build_provisioner(mode: str) -> Provisioner:
    if mode == "powershell":
        return PowerShellProvisioner()
    if mode == "dry-run":
        return DryRunProvisioner()
    raise ValueError(f"Unknown PROVISIONER mode: {mode!r} (use 'dry-run' or 'powershell')")
