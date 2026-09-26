"""Configuration loading and validation.

Config lives at ~/.config/claude-worklog/config.toml (XDG_CONFIG_HOME respected).
"""
from __future__ import annotations

import os
import re
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # Ubuntu 22.04 ships Python 3.10
    import tomli as tomllib


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


CONFIG_PATH = _xdg("XDG_CONFIG_HOME", ".config") / "claude-worklog" / "config.toml"
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "claude-worklog"  # logs, lock
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "claude-worklog"  # summarizer cwd
SUMMARIZER_CWD = DATA_DIR / "summarizer"

# Claude Code's default config dir. For this one we must NOT set CLAUDE_CONFIG_DIR,
# because the default install keeps some state outside it (e.g. ~/.claude.json).
DEFAULT_CLAUDE_DIR = Path.home() / ".claude"

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")

EXAMPLE_CONFIG = """\
# claude-worklog configuration
# Name this device. Must be unique across your machines (letters, digits, _ -).
device_name = "{device}"

# Local clone of your PRIVATE data repo (GitHub, GitLab or Bitbucket all work).
store_repo = "~/claude-worklog-data"

# One [[accounts]] block per Claude account used on THIS device.
# config_dir is where that account's Claude Code data lives.
[[accounts]]
name = "personal"
config_dir = "~/.claude"

# [[accounts]]
# name = "work"
# config_dir = "~/.claude-work"   # used via: CLAUDE_CONFIG_DIR=~/.claude-work claude

# Several accounts can share ONE folder (switching with /logout and /login).
# Each extra account is matched by the login recorded at session start; name them in
# the web UI (Settings > Detected accounts). The account without match_* is the
# folder's default and gets every session that matches no other account.
# [[accounts]]
# name = "client"
# config_dir = "~/.claude"
# match_account_id = "..."        # or: match_email = "you@example.com"

[collect]
misc_label = "R&D / misc"   # sessions run outside any git repo
include_commits = true      # read your own commits (git log --author=<your git email>)
max_prompts_per_session = 30
max_prompt_chars = 300

[report]
use_ai = true                 # summarize with `claude -p`; false = plain listing
summarizer_account = "personal"
claude_bin = "claude"         # use an absolute path if running from cron
ai_timeout_seconds = 180
"""


class ConfigError(Exception):
    """Raised for missing or invalid configuration."""


@dataclass(frozen=True)
class Account:
    name: str
    config_dir: Path
    # Optional: which login this account is when several accounts share config_dir.
    # Kept in the local config only; never written to records.
    match_account_id: str | None = None
    match_email: str | None = None

    @property
    def matched(self) -> bool:
        return bool(self.match_account_id or self.match_email)


@dataclass
class Config:
    device: str
    store_repo: Path
    accounts: list[Account]
    misc_label: str = "R&D / misc"
    include_commits: bool = True
    max_prompts: int = 30
    max_prompt_chars: int = 300
    use_ai: bool = True
    summarizer_account: str | None = None
    claude_bin: str = "claude"
    ai_timeout: int = 180
    path: Path = field(default=CONFIG_PATH)

    def account(self, name: str) -> Account | None:
        return next((a for a in self.accounts if a.name == name), None)

    def accounts_by_dir(self) -> dict[Path, list[Account]]:
        """Accounts grouped by config folder, in config file order."""
        out: dict[Path, list[Account]] = {}
        for a in self.accounts:
            out.setdefault(a.config_dir, []).append(a)
        return out

    def default_account(self, config_dir: Path) -> Account:
        """The folder's fallback: its first account without match_*, else its first account."""
        group = self.accounts_by_dir()[config_dir]
        return next((a for a in group if not a.matched), group[0])

    def resolve_account(self, config_dir: Path, account_id: str | None = None,
                        email: str | None = None) -> Account:
        """Configured account for a session in `config_dir` run by the given login."""
        group = self.accounts_by_dir()[config_dir]
        if account_id:
            hit = next((a for a in group if a.match_account_id == account_id), None)
            if hit:
                return hit
        if email:
            hit = next((a for a in group if a.match_email and a.match_email.casefold() == email.casefold()), None)
            if hit:
                return hit
        return self.default_account(config_dir)


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(p)).expanduser().resolve()


def _check_name(kind: str, value: object) -> str:
    if not isinstance(value, str) or not _NAME_RE.match(value):
        raise ConfigError(f"{kind} {value!r} must be 1-40 chars of letters, digits, '_' or '-'")
    return value


def _int(section: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = section.get(key, default)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ConfigError(f"{key} must be an integer between {lo} and {hi}")
    return v


def _match_id(name: str, v: object) -> str | None:
    if v is None or v == "":
        return None
    if not isinstance(v, str) or not _ACCOUNT_ID_RE.match(v):
        raise ConfigError(f"account {name}: match_account_id must be the account id from Claude Code")
    return v


def _match_email(name: str, v: object) -> str | None:
    if v is None or v == "":
        return None
    if not isinstance(v, str) or len(v) > 254 or not re.match(r"^[^@\s]+@[^@\s]+$", v):
        raise ConfigError(f"account {name}: match_email must be an e-mail address")
    return v


def _check_shared_dirs(accounts: list[Account]) -> None:
    """Accounts sharing a folder must be distinguishable, or sessions would be counted twice."""
    by_dir: dict[Path, list[Account]] = {}
    for a in accounts:
        by_dir.setdefault(a.config_dir, []).append(a)
    for d, group in by_dir.items():
        if len(group) < 2:
            continue
        defaults = [a.name for a in group if not a.matched]
        if len(defaults) > 1:
            raise ConfigError(f"accounts {', '.join(defaults)} share {d}: all but one need match_account_id "
                              "or match_email (the one without is that folder's default)")
        ids = [a.match_account_id for a in group if a.match_account_id]
        emails = [a.match_email.casefold() for a in group if a.match_email]
        if len(set(ids)) != len(ids) or len(set(emails)) != len(emails):
            raise ConfigError(f"two accounts in {d} match the same login")


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise ConfigError(f"No config at {path}. Run `claude-worklog init` first.")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc

    device = _check_name("device_name", raw.get("device_name"))
    if not isinstance(raw.get("store_repo"), str):
        raise ConfigError("store_repo is required")

    accounts: list[Account] = []
    for item in raw.get("accounts", []):
        if not isinstance(item, dict) or not isinstance(item.get("config_dir"), str):
            raise ConfigError("each [[accounts]] needs name and config_dir")
        name = _check_name("account name", item.get("name"))
        accounts.append(Account(name, _expand(item["config_dir"]),
                                _match_id(name, item.get("match_account_id")),
                                _match_email(name, item.get("match_email"))))
    if not accounts:
        raise ConfigError("configure at least one [[accounts]] block")
    if len({a.name for a in accounts}) != len(accounts):
        raise ConfigError("account names must be unique")
    _check_shared_dirs(accounts)

    col = raw.get("collect", {})
    rep = raw.get("report", {})
    cfg = Config(
        device=device,
        store_repo=_expand(raw["store_repo"]),
        accounts=accounts,
        misc_label=str(col.get("misc_label", "R&D / misc")),
        include_commits=bool(col.get("include_commits", True)),
        max_prompts=_int(col, "max_prompts_per_session", 30, 0, 500),
        max_prompt_chars=_int(col, "max_prompt_chars", 300, 20, 5000),
        use_ai=bool(rep.get("use_ai", True)),
        summarizer_account=rep.get("summarizer_account") or accounts[0].name,
        claude_bin=str(rep.get("claude_bin", "claude")),
        ai_timeout=_int(rep, "ai_timeout_seconds", 180, 10, 1800),
        path=path,
    )
    if cfg.account(cfg.summarizer_account) is None:
        raise ConfigError(f"summarizer_account {cfg.summarizer_account!r} is not a configured account")
    return cfg


def example_config() -> str:
    host = re.sub(r"[^A-Za-z0-9_-]", "-", socket.gethostname())[:40] or "device"
    return EXAMPLE_CONFIG.format(device=host)


# ------------------------------------------------------------------ writing
def home_relative(p: Path | str) -> str:
    s, home = str(p), str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + "/") else s


def under_home(p: Path, allow_home: bool = False) -> bool:
    home = Path.home().resolve()
    return (allow_home and p == home) or home in p.parents


def dump_config(cfg: Config) -> str:
    import json

    def q(s: object) -> str:
        return json.dumps(str(s), ensure_ascii=False)  # JSON strings are valid TOML basic strings

    def b(v: bool) -> str:
        return "true" if v else "false"

    out = ["# claude-worklog configuration (also editable from the web UI)",
           f"device_name = {q(cfg.device)}", f"store_repo = {q(home_relative(cfg.store_repo))}", ""]
    for a in cfg.accounts:
        out += ["[[accounts]]", f"name = {q(a.name)}", f"config_dir = {q(home_relative(a.config_dir))}"]
        if a.match_account_id:
            out.append(f"match_account_id = {q(a.match_account_id)}")
        if a.match_email:
            out.append(f"match_email = {q(a.match_email)}")
        out.append("")
    out += ["[collect]", f"misc_label = {q(cfg.misc_label)}", f"include_commits = {b(cfg.include_commits)}",
            f"max_prompts_per_session = {cfg.max_prompts}", f"max_prompt_chars = {cfg.max_prompt_chars}", "",
            "[report]", f"use_ai = {b(cfg.use_ai)}", f"summarizer_account = {q(cfg.summarizer_account)}",
            f"claude_bin = {q(cfg.claude_bin)}", f"ai_timeout_seconds = {cfg.ai_timeout}", ""]
    return "\n".join(out)


def save_config(cfg: Config, path: Path = CONFIG_PATH) -> None:
    """Validate by re-loading from a temp file, back up the old file, then swap atomically."""
    import shutil

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(dump_config(cfg), encoding="utf-8")
    tmp.chmod(0o600)
    try:
        load_config(tmp)
    except ConfigError:
        tmp.unlink(missing_ok=True)
        raise
    if path.exists():
        shutil.copy2(path, path.with_suffix(".toml.bak"))
    os.replace(tmp, path)
