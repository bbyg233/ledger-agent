from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def schedule_windows_reminder_sync(reminder_time: str, *, enabled: bool) -> bool:
    """Synchronize the Windows task without making the web request wait for it.

    The project remains usable on Linux and macOS: when WSL's PowerShell bridge
    is unavailable, the local SQLite preference still works and this is a no-op.
    """
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        return False
    project_dir = Path(__file__).resolve().parents[2]
    installer = project_dir / "scripts" / "install_windows_reminder.ps1"
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(installer),
    ]
    if enabled:
        port = os.environ.get("LEDGER_AGENT_PORT", "8000")
        command.extend(
            [
                "-ProjectPath",
                str(project_dir),
                "-WebUrl",
                f"http://127.0.0.1:{port}",
                "-Time",
                reminder_time,
            ]
        )
    else:
        command.append("-Uninstall")

    log_path = project_dir / ".financial_agent" / "reminder-scheduler.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log_file:
        subprocess.Popen(
            command,
            cwd=project_dir,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return True
