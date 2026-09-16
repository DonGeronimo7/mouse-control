"""Import-order regression tests for the standalone discovery command."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _fresh_python(code: str) -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    source_path = str(root / "src")
    env["PYTHONPATH"] = (
        source_path if not existing else os.pathsep.join((source_path, existing))
    )
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_discovery_cli_imports_in_fresh_python_process():
    """The console script must not rely on hardware modules being pre-imported."""

    _fresh_python("import mouse_control.discovery_cli")


def test_hidpp_driver_imports_before_hardware_registry():
    """Discovery may load HID++ before runtime backend selection without a cycle."""

    _fresh_python(
        "import mouse_control.hidpp_driver; "
        "from mouse_control.hardware import get_backend; "
        "assert callable(get_backend)"
    )


def test_full_access_requires_root_before_hardware_enumeration(monkeypatch, capsys):
    """Full-evidence mode must not silently fall back to partial user access."""

    from mouse_control import discovery_cli

    monkeypatch.setattr(discovery_cli.os, "geteuid", lambda: 1000)
    status = discovery_cli.main(["--full-access"])

    assert status == 77
    assert "requires root" in capsys.readouterr().err.lower()
