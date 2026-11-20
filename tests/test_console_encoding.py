"""The CLI never crashes on a character the console can't encode (✓ on a cp1252 pipe crashed `doctor` on Windows)."""
import io
import os
import subprocess
import sys

import pytest

from worklog import cli


class _Stream(io.TextIOWrapper):
    def __init__(self, encoding: str, tty: bool):
        super().__init__(io.BytesIO(), encoding=encoding)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty

    def written(self) -> bytes:
        self.flush()
        return self.buffer.getvalue()


@pytest.mark.parametrize("name", ["stdout", "stderr"])
def test_redirected_output_becomes_utf8(monkeypatch, name):
    s = _Stream("cp1252", tty=False)
    monkeypatch.setattr(sys, name, s)
    cli._safe_stdio()
    print("✓ config → ok", file=getattr(sys, name))
    assert s.written().decode("utf-8").strip() == "✓ config → ok"


def test_console_that_cannot_show_a_character_gets_a_question_mark(monkeypatch):
    s = _Stream("cp1252", tty=True)  # a real console keeps its encoding; only the error handling changes
    monkeypatch.setattr(sys, "stdout", s)
    cli._safe_stdio()
    print("✓ café", file=sys.stdout)
    assert s.written().decode("cp1252").strip() == "? café"


def test_utf8_output_is_left_alone(monkeypatch):
    s = _Stream("utf-8", tty=False)
    monkeypatch.setattr(sys, "stdout", s)
    cli._safe_stdio()
    assert s.encoding == "utf-8"
    print("✓", file=sys.stdout)
    assert s.written().decode("utf-8").strip() == "✓"


def test_no_streams_under_pythonw(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    cli._safe_stdio()  # must not raise


def test_doctor_on_a_cp1252_pipe(home):
    """End to end: the real command, piped, with the Windows ANSI code page forced on every OS."""
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    run = [sys.executable, "-m", "worklog.cli"]
    subprocess.run([*run, "init"], env=env, check=True, capture_output=True)
    r = subprocess.run([*run, "doctor"], env=env, capture_output=True, timeout=120)
    err = r.stderr.decode("utf-8", "replace")
    assert "UnicodeEncodeError" not in err, err
    out = r.stdout.decode("utf-8")
    assert "✓ config:" in out
