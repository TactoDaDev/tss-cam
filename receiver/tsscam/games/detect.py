"""Which supported game is running (for the receiver's Auto mode): one `tasklist` call, cheap enough every 2 s."""
from __future__ import annotations

import subprocess

PROCESSES = {
    "beamng": ("beamng.drive.x64.exe", "beamng.drive.exe"),
    "assetto": ("acs.exe", "acs_x86.exe"),
}


def running_game(tasklist_output: str | None = None) -> str | None:
    if tasklist_output is None:
        try:
            tasklist_output = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                             timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        except (OSError, subprocess.SubprocessError):
            return None
    names = {line.split('","')[0].strip('"').lower() for line in tasklist_output.splitlines() if line}
    for game, exes in PROCESSES.items():
        if names.intersection(exes):
            return game
    return None
