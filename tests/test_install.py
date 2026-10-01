from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install.py"
SPEC = importlib.util.spec_from_file_location("dbtalk_install", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
install: Any = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = install
SPEC.loader.exec_module(install)


def test_command_runner_includes_cli_failure_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["codex"],
            1,
            stdout="Loading marketplaces\n",
            stderr="invalid marketplace root\n",
        )

    monkeypatch.setattr(install.subprocess, "run", fake_run)

    with pytest.raises(install.SyncError) as error:
        install.CommandRunner(tmp_path).inspect(
            install.ClientTarget("codex", "codex"),
            ("plugin", "marketplace", "list", "--json"),
        )

    assert str(error.value) == (
        "codex CLI failed while running plugin marketplace list --json (exit code 1)\n"
        "stderr: invalid marketplace root\n"
        "stdout: Loading marketplaces"
    )
