"""Is the data repo connected to a remote, which one, is it private, and does syncing work?

* The store records the result of every pull and push here (`record`), so Settings can say
  "last upload 5 min ago" or show the last error.
* SSH host aliases (`git@work-github:me/repo.git`, set up in ~/.ssh/config to use another account's
  key) are resolved with `ssh -G`, so links and the privacy check use the real host, and Settings can
  say which key is used. `test_connection` also asks the host which account that key belongs to.
* For GitHub remotes, `visibility` asks GitHub's public API whether the repo can be seen
  without logging in: a 404 means private (good), "private": false means anyone can read it.
  Only the repo's name is sent, to the host that stores it anyway; the answer is kept 6 hours.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .config import STATE_DIR, Config

VISIBILITY_TTL = 6 * 3600
# What ssh -G lists when ~/.ssh/config names no key for a host.
_DEFAULT_KEYS = {"id_rsa", "id_ecdsa", "id_ecdsa_sk", "id_ed25519", "id_ed25519_sk", "id_xmss", "id_dsa"}
# Hosts that serve SSH on port 443 for the same site.
_SSH_HOSTS = {"ssh.github.com": "github.com", "altssh.gitlab.com": "gitlab.com",
              "altssh.bitbucket.org": "bitbucket.org"}
SITES = {"github.com": "GitHub", "gitlab.com": "GitLab", "bitbucket.org": "Bitbucket"}
_ssh_cache: dict[str, tuple[float, dict]] = {}


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


def _ssh_parts(url: str) -> tuple[str | None, str] | None:
    """(user, host-as-written) for SSH remotes (git@alias:o/r.git, ssh://git@alias/o/r.git), else None."""
    m = re.match(r"^ssh://(?:([^@/\s]+)@)?([^:/\s]+)", url)
    if m:
        return m.group(1), m.group(2)
    if "://" in url:
        return None
    m = re.match(r"^(?:([^@/\s]+)@)?([^:/\s]+):(?!//)", url)
    return (m.group(1), m.group(2)) if m and not re.match(r"^[A-Za-z]$", m.group(2)) else None  # not C:\path


def _ssh_command(repo: Path) -> list[str]:
    """The ssh git runs for this repo: core.sshCommand (one way to pick an account's key), else plain ssh."""
    code, cmd, _ = _git(repo, "config", "--get", "core.sshCommand")
    try:
        return shlex.split(cmd) if code == 0 and cmd else ["ssh"]
    except ValueError:
        return ["ssh"]


def _key_from_command(cmd: list[str]) -> str | None:
    for i, part in enumerate(cmd[:-1]):
        if part == "-i":
            return cmd[i + 1]
    return None


def resolve_ssh(alias: str, ssh: list[str] | None = None) -> dict:
    """{"hostname", "key"} for an SSH host as written in a remote URL. `ssh -G` only reads ~/.ssh/config:
    it connects nowhere. "key" is the key file the config picks, or None for ssh's default keys."""
    ssh = ssh or ["ssh"]
    cache_key = " ".join(ssh) + "|" + alias
    hit = _ssh_cache.get(cache_key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    out = {"hostname": alias, "key": _key_from_command(ssh)}
    if shutil.which(ssh[0]):
        try:
            r = subprocess.run([*ssh, "-G", alias], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=5, stdin=subprocess.DEVNULL)
            keys = []
            for line in r.stdout.splitlines() if r.returncode == 0 else []:
                k, _, v = line.partition(" ")
                if k == "hostname" and v:
                    out["hostname"] = v.strip()
                elif k == "identityfile":
                    keys.append(v.strip())
            picked = [k for k in keys if Path(k).name not in _DEFAULT_KEYS]
            if picked and not out["key"]:
                out["key"] = picked[0]
        except (OSError, subprocess.TimeoutExpired):
            pass
    out["hostname"] = _SSH_HOSTS.get(out["hostname"].lower(), out["hostname"].lower())
    _ssh_cache[cache_key] = (time.time(), out)
    return out


def _describe(repo: Path, url: str) -> dict | None:
    """The remote as Settings shows it: real host, slug, and how this computer signs in."""
    from .collector import sanitize_remote
    r = sanitize_remote(url) if url else None
    if not r:
        return None
    r = dict(r, via="", key=None, user=None)
    ssh = _ssh_parts(url.strip())
    if ssh:
        info = resolve_ssh(ssh[1], _ssh_command(repo))
        r["via"] = "ssh"
        r["key"] = info["key"]
        if info["hostname"] != r["host"]:
            r["alias"] = r["host"]
            r["host"] = info["hostname"]
    elif url.startswith("https://"):
        r["via"] = "https"
        m = re.match(r"^https://([^:@/\s]+)(?::[^@/\s]*)?@", url)
        r["user"] = m.group(1) if m else None  # never the password or token
    return r


def remote(cfg: Config) -> dict | None:
    """{"host", "slug", ...} of the data repo's origin (credentials dropped), or None. Local calls only."""
    if not (cfg.store_repo / ".git").exists():
        return None
    _, url, _ = _git(cfg.store_repo, "remote", "get-url", "origin")
    return _describe(cfg.store_repo, url)


def status(cfg: Config) -> dict:
    """Everything Settings shows about the data repo. Runs only local commands."""
    repo = cfg.store_repo
    out: dict = {"is_repo": (repo / ".git").exists(), "remote": None, "web_url": None, "branch": "",
                 "last_change": "", "waiting": 0, "push": last("push"), "pull": last("pull")}
    if not out["is_repo"]:
        return out
    _, url, _ = _git(repo, "remote", "get-url", "origin")
    r = _describe(repo, url)
    if r:
        out["remote"] = f"{r['host']}/{r['slug']}"
        out["host"], out["slug"] = r["host"], r["slug"]
        out["web_url"] = f"https://{r['host']}/{r['slug']}"
        out["alias"], out["key"], out["via"], out["url_user"] = r.get("alias"), r["key"], r["via"], r["user"]
        acct = _load().get("account") or {}
        out["account"] = acct.get("name") if acct.get("remote") == out["remote"] else None
    _, out["branch"], _ = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _, out["last_change"], _ = _git(repo, "log", "-1", "--format=%cr")
    if out["remote"]:
        code, n, _ = _git(repo, "rev-list", "--count", "@{u}..HEAD")
        if code != 0:  # never pushed (no upstream yet): everything is waiting
            code, n, _ = _git(repo, "rev-list", "--count", "HEAD")
        out["waiting"] = int(n) if code == 0 and n.isdigit() else 0
    return out


_WHO = (r"Hi ([\w.-]+)!",  # GitHub
        r"Welcome to GitLab, @([\w.-]+)!",  # GitLab
        r"logged in as ([\w.-]+)")  # Bitbucket


def ssh_account(cfg: Config, timeout: int = 20) -> str | None:
    """Which account the host says this computer's SSH key belongs to (GitHub, GitLab, Bitbucket), or None."""
    _, url, _ = _git(cfg.store_repo, "remote", "get-url", "origin")
    parts = _ssh_parts(url.strip()) if url else None
    if not parts:
        return None
    ssh = _ssh_command(cfg.store_repo)
    if not shutil.which(ssh[0]):
        return None
    target = f"{parts[0]}@{parts[1]}" if parts[0] else parts[1]
    try:
        r = subprocess.run([*ssh, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = f"{r.stdout}\n{r.stderr}"
    for pat in _WHO:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


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


def _main_line(err: str) -> str:
    """The line of git's error that says what went wrong (not "Please make sure you have the correct access")."""
    lines = [x.strip() for x in err.splitlines() if x.strip()]
    noise = ("please make sure", "and the repository exists", "fatal: could not read from remote")
    useful = [x for x in lines if not x.lower().startswith(noise)]
    return (useful or lines or ["no answer"])[0]


def test_connection(cfg: Config) -> tuple[bool, str]:
    """Can this computer reach the remote without a password prompt (git ls-remote), and as which account?"""
    st = status(cfg)
    who = ssh_account(cfg) if st.get("via") == "ssh" else st.get("url_user")
    data = _load()
    if who and st.get("remote"):
        data["account"] = {"remote": st["remote"], "name": who, "at": time.time()}
    else:
        data.pop("account", None)
    _save(data)
    site = SITES.get(st.get("host") or "", st.get("host") or "the host")
    as_who = f" as {who}" if who else ""
    code, _, err = _git(cfg.store_repo, "ls-remote", "--heads", "origin", timeout=30)
    if code == 0:
        return True, f"Connection works: this computer signs in to {site}{as_who} and can reach {st.get('remote')}."
    hint = _main_line(err)
    if who and ("not found" in err.lower() or "Permission denied" in err or "denied to" in err):
        hint = (f"{site} knows this computer's key as {who}, and {who} has no access to {st.get('slug')}. "
                f"Use the key of the account that owns the repo (see “Several accounts?” below), "
                f"or give {who} access to the repo")
    elif "Permission denied" in err or "publickey" in err:
        hint += " (the SSH key isn't allowed on this repo: add it as a deploy key with write access)"
    elif "could not read Username" in err or "terminal prompts disabled" in err:
        hint += " (this computer has no saved login for the repo: set up an SSH key, see the Full setup guide)"
    elif "timed out" in err or "Connection refused" in err:
        hint += (" (this network may block SSH on port 22: GitHub also takes SSH on port 443, see "
                 "“Several accounts?” below and set HostName ssh.github.com with Port 443)"
                 if st.get("host") == "github.com" else " (no internet, or this network blocks the connection)")
    elif "Could not resolve host" in err:
        hint += (" (no internet, or the host name is wrong; an SSH nickname like this must be defined in ~/.ssh/config)"
                 if st.get("via") == "ssh" and not st.get("alias") and "." not in (st.get("host") or ".") else
                 " (no internet, or the host name is wrong)")
    elif "not found" in err.lower() or "Repository not found" in err:
        hint += " (check the repo name, and that this key or account can see it)"
    return False, f"Can't reach your data repo: {hint[:300]}"
