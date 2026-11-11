import json
import subprocess

from app.parser import UserRequest
from app.provisioner import PowerShellProvisioner, _last_json_line

USER = UserRequest(first_name="A", last_name="B", ou="OU=X", username="a.b", password="S3cret!pass")


def test_password_is_sent_on_stdin_not_argv(monkeypatch):
    seen = {}

    def fake_run(cmd, input, **kwargs):
        seen["cmd"], seen["input"] = cmd, input
        return subprocess.CompletedProcess(cmd, 0, stdout='WARNING x\n{"success": true, "message": "User created"}\n', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = PowerShellProvisioner(executable="pwsh").create(USER)
    assert res.success and res.message == "User created"
    assert "S3cret!pass" not in " ".join(seen["cmd"])
    assert json.loads(seen["input"])["password"] == "S3cret!pass"


def test_no_json_output_is_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"))
    res = PowerShellProvisioner(executable="pwsh").create(USER)
    assert not res.success and res.message == "boom"


def test_timeout_is_failure(monkeypatch):
    def slow(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(subprocess, "run", slow)
    assert "timed out" in PowerShellProvisioner(executable="pwsh").create(USER).message


def test_last_json_line_ignores_noise():
    assert _last_json_line('noise\n{"a": 1}\n{broken\n') == {"a": 1}
    assert _last_json_line("nothing") is None
