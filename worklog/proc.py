"""Keyword arguments for starting child processes without a console window flashing on Windows.

Worklog's background work runs without a console: the scheduled collect and the login-time web UI run under
pythonw.exe, and the SessionEnd hook starts collect in the background. When a process with no console starts a
console program (git, ssh, schtasks, claude, pip), Windows gives that program a new console window, which
on Windows 11 opens Windows Terminal for a moment. Pass ``**NO_WINDOW`` to every subprocess.run/Popen whose
output is captured or discarded. Don't use it where the child should write to the user's own terminal.
"""
from __future__ import annotations

import sys

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

NO_WINDOW: dict = {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def detached() -> dict:
    """Popen keyword arguments for a process that outlives us and opens no window.

    On Windows this is deliberately not DETACHED_PROCESS: Windows ignores CREATE_NO_WINDOW when that flag is
    set, so the child has no console at all and every console program it starts opens a window of its own.
    With CREATE_NO_WINDOW alone the child gets a hidden console that its own children share.
    """
    if sys.platform == "win32":
        return {"creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW, "close_fds": True}
    return {"start_new_session": True}
