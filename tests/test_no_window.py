"""No console window may flash on Windows when background work starts git, ssh, schtasks or claude.

Background runs have no console (pythonw.exe, Task Scheduler, the SessionEnd hook's detached collect), so
every console program they start would get a new window unless the call passes CREATE_NO_WINDOW.
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

from worklog import proc

PACKAGE = Path(__file__).resolve().parent.parent / "worklog"
# demo.py runs in the user's own terminal and should print there, so it is the one exception
EXEMPT = {"demo.py"}
OK_SPREADS = {"NO_WINDOW", "_detached", "detached"}


def _spread_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""


def test_every_child_process_opens_no_window():
    missing = []
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name in EXEMPT:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"run", "Popen", "call", "check_call", "check_output"}
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
                continue
            spreads = {_spread_name(k.value) for k in node.keywords if k.arg is None}
            if not spreads & OK_SPREADS and not any(k.arg == "creationflags" for k in node.keywords):
                missing.append(f"{path.name}:{node.lineno}")
    assert not missing, f"pass **NO_WINDOW (worklog/proc.py) to these calls: {missing}"


def test_detached_has_a_hidden_console_on_windows(monkeypatch):
    monkeypatch.setattr(proc.sys, "platform", "win32")
    flags = proc.detached()["creationflags"]
    # Windows ignores CREATE_NO_WINDOW next to DETACHED_PROCESS, and a process without any console
    # opens a window for every console program it starts.
    assert flags & proc.CREATE_NO_WINDOW and not flags & 0x00000008
    monkeypatch.setattr(proc.sys, "platform", "linux")
    assert proc.detached() == {"start_new_session": True}


def test_no_window_is_empty_off_windows():
    assert proc.NO_WINDOW == ({"creationflags": 0x08000000} if sys.platform == "win32" else {})


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console behaviour")
def test_detached_child_gives_its_children_a_hidden_console(tmp_path):
    """A detached worker's own subprocess calls share its windowless console, so they get no window either."""
    report = (  # prints: <processes attached to my console> <my console window is visible: 0/1>
        "import ctypes; k = ctypes.windll.kernel32; buf = (ctypes.c_ulong * 64)(); w = k.GetConsoleWindow(); "
        "print(k.GetConsoleProcessList(buf, 64), ctypes.windll.user32.IsWindowVisible(w) if w else 0)")
    probe = tmp_path / "probe.py"
    out = tmp_path / "out.txt"
    probe.write_text(
        "import subprocess, sys\n"
        f"report = {report!r}\n"
        "exec(report.replace('print(', 'own = ('))\n"
        "inner = subprocess.run([sys.executable, '-c', report], capture_output=True, text=True).stdout.split()\n"
        f"open({str(out)!r}, 'w').write(f'{{own[0]}} {{own[1]}} {{inner[0]}} {{inner[1]}}')\n",
        encoding="utf-8")
    subprocess.Popen([sys.executable, str(probe)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **proc.detached()).wait(timeout=60)
    own_procs, own_visible, inner_procs, inner_visible = map(int, out.read_text().split())
    assert own_procs >= 1 and own_visible == 0  # the worker has a console, and no window shows it
    assert inner_procs >= 2 and inner_visible == 0  # an unflagged child joins that console: no new window
