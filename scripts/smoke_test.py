"""Install a built wheel into a fresh, empty environment and use it the way a new user would.

    python scripts/smoke_test.py dist/worklog_for_claude_code-X.Y.Z-py3-none-any.whl

Checks, in order:
  1. the wheel holds exactly the source files of this checkout (same SHA-256), nothing more
  2. it installs into a new virtual environment, with no other packages needed (Python 3.11+)
  3. `claude-worklog --version` prints the version in the wheel's name
  4. `claude-worklog demo` starts, serves its pages, refuses a foreign Host, and cleans up when stopped
  5. `init` writes a private config and `doctor` runs, in a throwaway home folder
  6. uninstalling removes the command

Standard library only, so it runs the same on Linux, macOS and Windows (CI and release use it).
"""
from __future__ import annotations

import hashlib
import http.client
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIN = os.name == "nt"


def step(msg: str) -> None:
    print(f"\n== {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"✗ {msg}", flush=True)
    sys.exit(1)


def sha(data: bytes) -> str:
    """SHA-256 of a file's content. Line endings are normalised, because git on Windows checks out text files
    with CRLF while the wheel is built with LF: same code, different bytes."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def check_contents(wheel: Path) -> str:
    step("1. The wheel matches this source tree")
    with zipfile.ZipFile(wheel) as z:
        packed = {n: sha(z.read(n)) for n in z.namelist() if n.startswith("worklog/") and not n.endswith("/")}
    source = {p.relative_to(ROOT).as_posix(): sha(p.read_bytes())
              for p in (ROOT / "worklog").rglob("*") if p.is_file() and "__pycache__" not in p.parts
              and p.suffix != ".pyc"}
    missing, extra = sorted(set(source) - set(packed)), sorted(set(packed) - set(source))
    changed = sorted(n for n in set(source) & set(packed) if source[n] != packed[n])
    if missing or extra or changed:
        fail(f"wheel differs from source: missing={missing} extra={extra} changed={changed}")
    print(f"✓ {len(packed)} files, all identical to the source (SHA-256, line endings ignored)")
    version = wheel.name.split("-")[1]
    print(f"✓ version in the file name: {version}")
    return version


def run(cmd: list, env=None, ok=(0,), timeout=120) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, timeout=timeout)
    if r.returncode not in ok:
        fail(f"{' '.join(map(str, cmd))} exited {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(port: int, path: str, host: str | None = None) -> tuple[int, str]:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": host or f"localhost:{port}"})
    r = c.getresponse()
    out = r.status, r.read().decode("utf-8", "replace")
    c.close()
    return out


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # the Windows console may not be UTF-8; never crash on ✓ or ✗
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if len(sys.argv) != 2 or not sys.argv[1].endswith(".whl"):
        fail("usage: python scripts/smoke_test.py path/to/worklog_for_claude_code-*.whl")
    wheel = Path(sys.argv[1]).resolve()
    version = check_contents(wheel)
    work = Path(tempfile.mkdtemp(prefix="worklog-smoke-"))
    home = work / "home"
    home.mkdir()
    try:
        step("2. Install into a fresh virtual environment")
        venv.EnvBuilder(with_pip=True, clear=True).create(work / "venv")
        bindir = work / "venv" / ("Scripts" if WIN else "bin")
        py = bindir / ("python.exe" if WIN else "python")
        run([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", str(wheel)], timeout=300)
        installed = run([py, "-m", "pip", "list", "--format=freeze", "--disable-pip-version-check"]).stdout
        others = [x for x in installed.splitlines() if x.split("==")[0].lower() not in
                  ("worklog-for-claude-code", "pip", "setuptools", "wheel", "tomli")]
        if others:
            fail(f"unexpected packages were installed: {others}")
        exe = shutil.which("claude-worklog", path=str(bindir))
        if not exe:
            fail("the claude-worklog command was not installed")
        print(f"✓ installed, no other packages needed ({exe})")

        step("3. Version")
        out = run([exe, "--version"]).stdout.strip()
        if out != version:
            fail(f"--version printed {out!r}, expected {version!r}")
        print(f"✓ claude-worklog --version → {out}")

        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
               "XDG_STATE_HOME": str(home / ".local/state"), "XDG_DATA_HOME": str(home / ".local/share"),
               "APPDATA": str(home / "AppData/Roaming"), "LOCALAPPDATA": str(home / "AppData/Local"),
               "PYTHONIOENCODING": "utf-8"}
        env.pop("CLAUDE_CONFIG_DIR", None)

        step("4. The demo starts and serves its pages")
        port = free_port()
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if WIN else 0
        demo = subprocess.Popen([exe, "demo", "--port", str(port)], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                creationflags=flags)
        try:
            for _ in range(120):
                try:
                    if get(port, "/")[0] == 200:
                        break
                except OSError:
                    pass
                if demo.poll() is not None:
                    fail(f"the demo stopped early:\n{demo.stdout.read()}")
                time.sleep(0.5)
            else:
                fail("the demo did not answer within 60 seconds")
            for path in ("/", "/trends", "/timesheet", "/ask?q=refund", "/share", "/share.svg", "/year",
                         "/settings", "/sources", "/api/status"):
                status, body = get(port, path)
                if status != 200:
                    fail(f"GET {path} → {status}")
                if path == "/api/status" and f'"version": "{version}"' not in body:
                    fail(f"/api/status does not report {version}: {body[:200]}")
            print("✓ 10 pages answer 200, and the page reports the same version")
            if get(port, "/", host="evil.example")[0] != 403:
                fail("a foreign Host header was not refused")
            print("✓ a foreign Host header is refused")
        finally:
            if demo.poll() is None:
                demo.send_signal(signal.CTRL_BREAK_EVENT if WIN else signal.SIGTERM)
                try:
                    demo.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    demo.kill()
                    demo.wait()
        print("✓ the demo stopped")

        step("5. init and doctor in an empty home folder")
        run([exe, "init"], env=env)
        cfg = home / ".config/claude-worklog/config.toml"
        if not cfg.exists():
            cfg = next(iter(home.rglob("config.toml")), None)
        if not cfg:
            fail("init did not write a config file")
        if not WIN and cfg.stat().st_mode & 0o077:
            fail(f"config is readable by others: {oct(cfg.stat().st_mode)}")
        print(f"✓ init wrote {cfg.relative_to(home)}" + ("" if WIN else " (only you can read it)"))
        doc = run([exe, "doctor"], env=env, ok=(0, 1))
        if "Traceback" in doc.stdout + doc.stderr:
            fail(f"doctor crashed:\n{doc.stdout}\n{doc.stderr}")
        print(f"✓ doctor ran and explained what is missing (exit {doc.returncode}, no crash)")

        step("6. Uninstall")
        run([py, "-m", "pip", "uninstall", "--yes", "--quiet", "worklog-for-claude-code"])
        if shutil.which("claude-worklog", path=str(bindir)):
            fail("the command is still there after uninstalling")
        print("✓ uninstalled; the command is gone")
        print(f"\nAll checks passed for {wheel.name}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
