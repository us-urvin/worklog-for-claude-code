"""Git-backed central store (any host: GitHub, GitLab, Bitbucket, self-hosted).

Layout (every path is device-specific, so devices never conflict):
    records/<YYYY-MM-DD>/<device>/<account>/<session>.json
    reports/<YYYY-MM-DD>/<device>.md
"""
from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .config import STATE_DIR, ConfigError

log = logging.getLogger(__name__)

# Commits in the data repo use a neutral identity (no personal e-mail stored).
_IDENTITY = ["-c", "user.name=claude-worklog", "-c", "user.email=claude-worklog@localhost"]


class GitStore:
    def __init__(self, path: Path):
        self.path = path
        if not (path / ".git").exists():
            raise ConfigError(
                f"{path} is not a git repo. Create a PRIVATE repo on your git host and clone it there."
            )
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"  # never hang waiting for a password (hooks/cron)
        env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")
        self._env = env

    # ---------------------------------------------------------------- locking
    @contextmanager
    def lock(self, timeout: float = 90.0):
        """Serialize hook, cron and manual runs on this machine."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "store.lock", "w") as fh:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES) or time.monotonic() > deadline:
                        raise TimeoutError("another claude-worklog run holds the lock") from exc
                    time.sleep(1)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    # ------------------------------------------------------------------- git
    def _git(self, *args: str, timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True, text=True, timeout=timeout, check=check, env=self._env,
        )

    def _has_remote(self) -> bool:
        return bool(self._git("remote", check=False).stdout.strip())

    def pull(self) -> None:
        if not self._has_remote():
            return
        try:
            self._git("pull", "--rebase", "--autostash", timeout=90)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            # Offline, empty repo, or first run: work locally, sync next time.
            detail = getattr(exc, "stderr", "") or str(exc)
            log.warning("git pull failed (continuing locally): %s", detail.strip()[:300])
            if (self.path / ".git" / "rebase-merge").exists() or (self.path / ".git" / "rebase-apply").exists():
                self._git("rebase", "--abort", check=False)

    def commit_and_push(self, message: str) -> None:
        dirs = [d for d in ("records", "reports") if (self.path / d).exists()]
        if not dirs:
            return
        self._git("add", "-A", "--", *dirs)
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return  # nothing staged
        self._git(*_IDENTITY, "commit", "-q", "-m", message)
        if not self._has_remote():
            log.info("committed locally (no remote configured)")
            return
        try:
            self._git("push", "-q", "-u", "origin", "HEAD", timeout=90)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            log.warning("git push failed; commit kept locally and retried next run: %s", detail.strip()[:300])

    # ----------------------------------------------------------------- files
    def _target(self, rel: str) -> Path:
        target = (self.path / rel).resolve()
        if self.path.resolve() not in target.parents:
            raise ValueError(f"refusing to write outside store: {rel}")
        return target

    def write_text(self, rel: str, text: str) -> bool:
        """Atomically write; returns False if content is unchanged."""
        target = self._target(rel)
        if target.exists() and target.read_text(encoding="utf-8") == text:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
        return True

    def move(self, src_rel: str, dst_rel: str) -> None:
        """Rename a file inside the store (commit_and_push stages it as a rename)."""
        src, dst = self._target(src_rel), self._target(dst_rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        try:
            src.parent.rmdir()  # drop the now-empty account folder, if it is empty
        except OSError:
            pass

    def write_json(self, rel: str, data: dict) -> bool:
        return self.write_text(rel, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    def iter_json(self, rel_dir: str):
        base = self.path / rel_dir
        for p in sorted(base.rglob("*.json")) if base.exists() else []:
            try:
                yield json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("skipping unreadable record %s: %s", p, exc)
