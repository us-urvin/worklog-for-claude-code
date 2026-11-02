"""Is the data repo connected to a remote, which one, is it private, and does syncing work?

* The store records the result of every pull and push here (`record`), so Settings can say
  "last upload 5 min ago" or show the last error.
* For GitHub remotes, `visibility` asks GitHub's public API whether the repo can be seen
  without logging in: a 404 means private (good), "private": false means anyone can read it.
  Only the repo's name is sent, to the host that stores it anyway; the answer is kept 6 hours.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .config import STATE_DIR, Config

VISIBILITY_TTL = 6 * 3600


def _path() -> Path:
    return STATE_DIR / "sync.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # status bookkeeping must never break a sync


def record(kind: str, ok: bool, error: str = "") -> None:
    """Remember the outcome of a "pull" or "push" (called by the store)."""
    data = _load()
    data[kind] = {"at": time.time(), "ok": ok, "error": error.strip()[:300]}
    _save(data)


def last(kind: str) -> dict | None:
    v = _load().get(kind)
    return v if isinstance(v, dict) else None


def _git(repo: Path, *args: str, timeout: int = 10) -> tuple[int, str, str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def remote(cfg: Config) -> dict | None:
    """{"host", "slug"} of the data repo's origin (credentials dropped), or None. One local git call."""
    from .collector import sanitize_remote
    if not (cfg.store_repo / ".git").exists():
        return None
    _, url, _ = _git(cfg.store_repo, "remote", "get-url", "origin")
    return sanitize_remote(url) if url else None


def status(cfg: Config) -> dict:
    """Everything Settings shows about the data repo. Runs only local git commands."""
    from .collector import sanitize_remote
    repo = cfg.store_repo
    out: dict = {"is_repo": (repo / ".git").exists(), "remote": None, "web_url": None, "branch": "",
                 "last_change": "", "waiting": 0, "push": last("push"), "pull": last("pull")}
    if not out["is_repo"]:
        return out
    _, url, _ = _git(repo, "remote", "get-url", "origin")
    r = sanitize_remote(url) if url else None
    if r:
        out["remote"] = f"{r['host']}/{r['slug']}"
        out["host"], out["slug"] = r["host"], r["slug"]
        out["web_url"] = f"https://{r['host']}/{r['slug']}"
    _, out["branch"], _ = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _, out["last_change"], _ = _git(repo, "log", "-1", "--format=%cr")
    if out["remote"]:
        code, n, _ = _git(repo, "rev-list", "--count", "@{u}..HEAD")
        if code != 0:  # never pushed (no upstream yet): everything is waiting
            code, n, _ = _git(repo, "rev-list", "--count", "HEAD")
        out["waiting"] = int(n) if code == 0 and n.isdigit() else 0
    return out


def cached_visibility(host: str | None, slug: str | None) -> str | None:
    """The last answer for this repo if it is fresh, "unknown" for non-GitHub hosts, else None (not checked yet)."""
    if host != "github.com" or not slug:
        return "unknown"
    cached = _load().get("visibility") or {}
    if cached.get("slug") == slug and time.time() - float(cached.get("at", 0)) < VISIBILITY_TTL:
        return str(cached.get("value", "unknown"))
    return None


def visibility(host: str | None, slug: str | None, force: bool = False, timeout: float = 5.0) -> str:
    """"private", "public" or "unknown" (not GitHub, offline, or rate-limited). Cached for 6 hours."""
    if host != "github.com" or not slug:
        return "unknown"
    data = _load()
    cached = data.get("visibility") or {}
    if not force and cached.get("slug") == slug and time.time() - float(cached.get("at", 0)) < VISIBILITY_TTL:
        return str(cached.get("value", "unknown"))
    value = "unknown"
    try:
        req = Request(f"https://api.github.com/repos/{slug}",
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "claude-worklog"})
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
            info = json.loads(resp.read(200_000).decode("utf-8"))
            value = "private" if info.get("private") else "public"
    except HTTPError as exc:
        value = "private" if exc.code == 404 else "unknown"  # 404: not visible without logging in
    except (OSError, ValueError):
        value = "unknown"
    data["visibility"] = {"slug": slug, "value": value, "at": time.time()}
    _save(data)
    return value


def test_connection(cfg: Config) -> tuple[bool, str]:
    """Can this computer reach the remote without a password prompt? (git ls-remote)"""
    code, _, err = _git(cfg.store_repo, "ls-remote", "--heads", "origin", timeout=30)
    if code == 0:
        return True, "Connection works: this computer can reach your data repo."
    hint = err.splitlines()[-1] if err else "no answer"
    if "Permission denied" in err or "publickey" in err:
        hint += " (the SSH key isn't allowed on this repo: add it as a deploy key with write access)"
    elif "could not read Username" in err or "terminal prompts disabled" in err:
        hint += " (this computer has no saved login for the repo: set up an SSH key, see the Full setup guide)"
    elif "Could not resolve host" in err:
        hint += " (no internet, or the host name is wrong)"
    elif "not found" in err.lower() or "Repository not found" in err:
        hint += " (check the repo name, and that this key or account can see it)"
    return False, f"Can't reach your data repo: {hint[:250]}"
