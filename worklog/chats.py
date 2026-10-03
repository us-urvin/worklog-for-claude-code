"""claude.ai chat exports -> redacted per-chat, per-day records in the store.

claude.ai has no API for chat history. Its data export (Settings > Privacy > Export data)
e-mails links to several zips; `conversations-NNN.zip` holds `conversations.json`:

    [{uuid, name, summary, created_at, updated_at, account: {uuid},
      chat_messages: [{uuid, sender: "human" | "assistant", text, content: [blocks],
                       created_at, attachments, files, parent_message_uuid}]}]

The format is not a documented stable API, so parsing is defensive. Only your own typed
messages (redacted, shortened), chat titles, tool names and edited file paths are kept.
Claude's replies, the chat `summary`, thinking, tool input/output and attachment contents
are never read into a record. There are no token counts in the export.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .collector import _move_stale, _transcripts
from .config import STATE_DIR, Config, home_relative
from .identity import _save_state, ref
from .parser import parse_ts, zero_tokens
from .redact import redact
from .store import GitStore

log = logging.getLogger(__name__)
SCHEMA = 1
SOURCE = "claude.ai"
CHAT_PROJECT = "claude.ai chat"
MAX_JSON_BYTES = 512 * 1024 * 1024  # uncompressed conversations.json; real exports are a few MB
MAX_TITLE_CHARS = 120
SETTLE_SECONDS = 10  # a download modified more recently than this may still be in progress
SANDBOX = "/home/claude/"  # where Claude's own computer in claude.ai keeps project files
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "create_file", "str_replace",
              "str_replace_editor", "str_replace_based_edit_tool"}
_PATH_KEYS = ("file_path", "path", "notebook_path")
_SAFE = re.compile(r"[^A-Za-z0-9_-]")
_TOOL_ID = re.compile(r"toolu_[A-Za-z0-9]+")


class ExportError(Exception):
    """The file is not a usable claude.ai conversations export."""


# ------------------------------------------------------------------ reading
def _read_capped(fh, name: str) -> bytes:
    data = fh.read(MAX_JSON_BYTES + 1)  # read one byte past the cap: zip headers can lie about sizes
    if len(data) > MAX_JSON_BYTES:
        raise ExportError(f"{name} is larger than {MAX_JSON_BYTES // (1024 * 1024)} MB; refusing to read it")
    return data


def _candidates(path: Path) -> list[Path]:
    if path.is_dir():
        found = sorted(p for p in path.iterdir() if p.is_file() and p.name.startswith("conversations")
                       and p.suffix in (".zip", ".json"))
        if not found:
            raise ExportError(f"no conversations-*.zip or conversations.json in {path}")
        return found
    if not path.is_file():
        raise ExportError(f"{path} does not exist")
    return [path]


def _blobs(path: Path) -> list[tuple[str, bytes]]:
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                members = [i for i in zf.infolist() if not i.is_dir() and Path(i.filename).name == "conversations.json"]
                if not members:
                    raise ExportError(f"{path.name} has no conversations.json. Use the conversations-NNN.zip "
                                      "download from the export e-mail.")
                out = []
                for info in members:
                    with zf.open(info) as fh:
                        out.append((f"{path.name}:{info.filename}", _read_capped(fh, info.filename)))
                return out
        except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError) as exc:
            raise ExportError(f"{path.name} is not a readable zip file: {exc}") from exc
    with path.open("rb") as fh:
        return [(path.name, _read_capped(fh, path.name))]


def _valid(conv: object) -> bool:
    return isinstance(conv, dict) and isinstance(conv.get("uuid"), str) and isinstance(conv.get("chat_messages"), list)


def _parse(name: str, data: bytes) -> tuple[list[dict], int]:
    try:
        convs = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(convs, list):
        raise ExportError(f"{name} is not a claude.ai conversations export (expected a list of chats)")
    good = [c for c in convs if _valid(c)]
    if convs and not good:
        if any(isinstance(c, dict) and "mapping" in c for c in convs):
            raise ExportError(f"{name} looks like a ChatGPT export, not a claude.ai one")
        raise ExportError(f"{name} is not a claude.ai conversations export (no chat_messages found)")
    return good, len(convs) - len(good)


def _newer(a: dict, b: dict) -> bool:
    ta, tb = parse_ts(a.get("updated_at")), parse_ts(b.get("updated_at"))
    return tb is None or (ta is not None and ta > tb)


def load_conversations(path: Path) -> tuple[list[dict], int, list[Path]]:
    """(chats, malformed chat count, files read). Parts are merged; a chat in two parts is kept once."""
    files = _candidates(path)
    merged: dict[str, dict] = {}
    bad = 0
    for f in files:
        for name, data in _blobs(f):
            convs, n_bad = _parse(name, data)
            bad += n_bad
            for c in convs:
                old = merged.get(c["uuid"])
                if old is None or _newer(c, old):
                    merged[c["uuid"]] = c
    return list(merged.values()), bad, files


# ------------------------------------------------------------------ per-day aggregation
@dataclass
class ChatDay:
    uuid: str
    day: date
    title: str
    account_uuid: str | None
    updated_at: str
    first: datetime | None = None
    last: datetime | None = None
    messages: int = 0
    prompts: list[str] = field(default_factory=list)
    tool_calls: Counter = field(default_factory=Counter)
    files: set[str] = field(default_factory=set)
    folders: Counter = field(default_factory=Counter)
    tool_ids: set[str] = field(default_factory=set)  # only used to spot Claude Code sessions; never written

    def touch(self, ts: datetime) -> None:
        self.first = ts if self.first is None or ts < self.first else self.first
        self.last = ts if self.last is None or ts > self.last else self.last


def _file(cd: ChatDay, path: str) -> None:
    if path.startswith(SANDBOX):
        parts = path[len(SANDBOX):].split("/")
        if len(parts) >= 2 and parts[0] and not parts[0].startswith("."):
            cd.folders[parts[0]] += 1
            cd.files.add("/".join(parts[1:]))
            return
        cd.files.add(path[len(SANDBOX):])
        return
    cd.files.add(home_relative(path))


def split_days(conv: dict, since: date | None = None) -> tuple[dict[date, ChatDay], int]:
    """Group one chat's messages by local day. Returns (days, malformed message count)."""
    days: dict[date, ChatDay] = {}
    seen: dict[date, set[str]] = {}
    bad = 0
    title = conv.get("name") if isinstance(conv.get("name"), str) else ""
    acct = conv.get("account") if isinstance(conv.get("account"), dict) else {}
    updated = conv.get("updated_at") if isinstance(conv.get("updated_at"), str) else ""
    for m in conv["chat_messages"]:
        if not isinstance(m, dict):
            bad += 1
            continue
        ts = parse_ts(m.get("created_at"))
        if ts is None:
            bad += 1
            continue
        day = ts.date()
        if since and day < since:
            continue
        cd = days.get(day)
        if cd is None:
            uid = acct.get("uuid") if isinstance(acct.get("uuid"), str) else None
            cd = days[day] = ChatDay(conv["uuid"], day, title, uid, updated)
            seen[day] = set()
        cd.touch(ts)
        cd.messages += 1
        if m.get("sender") == "human":
            text = m.get("text").strip() if isinstance(m.get("text"), str) else ""
            if text and text not in seen[day]:  # empty = tool-loop turn; repeats = edited/retried branches
                seen[day].add(text)
                cd.prompts.append(text)
        for block in m.get("content") if isinstance(m.get("content"), list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name") or "unknown")[:80]
            cd.tool_calls[name] += 1
            if isinstance(block.get("id"), str) and block["id"].startswith("toolu_"):
                cd.tool_ids.add(block["id"])
            inp = block.get("input") if isinstance(block.get("input"), dict) else {}
            if name in EDIT_TOOLS:
                path = next((inp[k] for k in _PATH_KEYS if isinstance(inp.get(k), str)), None)
                if path:
                    _file(cd, path)
    return days, bad


def build_record(cd: ChatDay, account: str, cfg: Config) -> dict | None:
    if not cd.prompts and not cd.tool_calls:
        return None  # only Claude's messages that day: nothing of yours to log
    folder = cd.folders.most_common(1)[0][0] if cd.folders else None
    title = redact(" ".join(cd.title.split()))[:MAX_TITLE_CHARS] or "Untitled chat"
    return {
        "schema": SCHEMA,
        "source": SOURCE,
        "session_id": f"chat-{cd.uuid}",
        "title": title,
        "date": cd.day.isoformat(),
        "account": account,
        "device": cfg.device,
        "project": {"kind": "chat", "name": folder or CHAT_PROJECT, "folder": folder, "path": None,
                    "remote": None, "branches": []},
        "start": cd.first.isoformat(timespec="seconds") if cd.first else None,
        "end": cd.last.isoformat(timespec="seconds") if cd.last else None,
        "span_minutes": round((cd.last - cd.first).total_seconds() / 60) if cd.first and cd.last else 0,
        "api_calls": 0,
        "tokens": zero_tokens(),  # the export has no usage data
        "tokens_by_model": {},
        "message_count": cd.messages,
        "prompt_count": len(cd.prompts),
        "prompts": [redact(p)[: cfg.max_prompt_chars] for p in cd.prompts[: cfg.max_prompts]],
        "files_touched": sorted(cd.files)[:200],
        "tool_calls": dict(cd.tool_calls),
        "commits": [],
        "conversation_updated_at": cd.updated_at,
    }


def local_tool_ids(cfg: Config, days: set[date]) -> set[str]:
    """Tool-call ids in this device's Claude Code transcripts touched since the earliest of `days`.

    A chat containing one of them is a Claude Code session that the collector already records,
    with real token counts, so the import skips it.
    """
    if not days:
        return set()
    ids: set[str] = set()
    for config_dir, group in cfg.accounts_by_dir().items():
        for p in _transcripts(config_dir, "/".join(a.name for a in group), min(days)):
            try:
                with p.open("r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if "toolu_" in line:
                            ids.update(_TOOL_ID.findall(line))
            except OSError as exc:
                log.warning("cannot read %s: %s", p, exc)
    return ids


# ------------------------------------------------------------------ local state
def state_path() -> Path:
    return STATE_DIR / "chats.json"


def load_state() -> dict:
    state = {"imported": {}, "rejected": {}, "accounts": {}, "last_import": None}
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return state
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable %s: %s", state_path(), exc)
        return state
    if isinstance(data, dict):
        for k in ("imported", "rejected", "accounts"):
            if isinstance(data.get(k), dict):
                state[k] = data[k]
        if isinstance(data.get("last_import"), dict):
            state["last_import"] = data["last_import"]
    return state


def save_state(state: dict) -> None:
    _save_state(state, state_path())


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ import
@dataclass
class ImportResult:
    chats: int = 0
    days: int = 0
    written: int = 0
    duplicates: int = 0  # Claude Code sessions already recorded from local transcripts
    stale: int = 0  # an older export than what the store already holds
    bad: int = 0  # malformed chats or messages, skipped
    account: str = ""

    def message(self) -> str:
        out = (f"Imported {self.chats} chat(s) as {self.days} day record(s) for {self.account}; "
               f"{self.written} new or updated")
        extra = [f"{n} {what}" for n, what in ((self.duplicates, "already counted as Claude Code sessions"),
                                               (self.stale, "kept because the store has a newer copy"),
                                               (self.bad, "malformed entries skipped")) if n]
        return out + (" (" + ", ".join(extra) + ")" if extra else "") + "."


def _account_for(cfg: Config, convs: list[dict], account: str | None, state: dict) -> str:
    refs = {ref(c["account"]["uuid"]) for c in convs
            if isinstance(c.get("account"), dict) and isinstance(c["account"].get("uuid"), str)}
    if account:
        if cfg.account(account) is None:
            raise ExportError(f"no account named {account!r} in this device's config")
        for r in refs:
            state["accounts"][r] = account  # remembered for later imports of this claude.ai login
        return account
    remembered = {state["accounts"].get(r) for r in refs} - {None}
    if len(remembered) == 1:
        name = remembered.pop()
        if cfg.account(name) is not None:
            return name
    return cfg.chats_account


def import_chats(cfg: Config, path: Path, account: str | None = None, since: date | None = None) -> ImportResult:
    convs, bad, files = load_conversations(path)
    state = load_state()
    res = ImportResult(chats=len(convs), bad=bad, account=_account_for(cfg, convs, account, state))
    chat_days: list[ChatDay] = []
    for c in convs:
        days, n_bad = split_days(c, since)
        res.bad += n_bad
        chat_days.extend(days.values())
    known = local_tool_ids(cfg, {cd.day for cd in chat_days if cd.tool_ids})

    store = GitStore(cfg.store_repo)
    with store.lock():
        store.pull()
        for cd in chat_days:
            if cd.tool_ids & known:
                res.duplicates += 1
                continue
            record = build_record(cd, res.account, cfg)
            if record is None:
                continue
            res.days += 1
            fname = f"chat-{_SAFE.sub('_', cd.uuid)}.json"
            rel = f"records/{cd.day.isoformat()}/{cfg.device}/{res.account}/{fname}"
            try:
                old = json.loads((store.path / rel).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old = None
            if isinstance(old, dict) and _newer({"updated_at": old.get("conversation_updated_at")},
                                                {"updated_at": cd.updated_at}):
                res.stale += 1
                continue
            res.written += _move_stale(store, cd.day, cfg.device, res.account, fname)  # account mapping changed
            res.written += store.write_json(rel, record)
        if res.written:
            store.commit_and_push(f"import chats {cfg.device}: {res.written} record(s)")

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for f in files:
        try:
            st = f.stat()
            state["imported"][digest(f)] = {"name": f.name, "size": st.st_size, "at": now, "chats": res.chats}
        except OSError:
            pass
    state["last_import"] = {"at": now, "chats": res.chats, "records": res.days, "account": res.account,
                            "file": files[0].name if len(files) == 1 else f"{len(files)} files"}
    save_state(state)
    log.info("chat import from %s: %s", path, res.message())
    return res


def pending_downloads(cfg: Config, now: float | None = None) -> list[Path]:
    """Finished, not yet imported conversation exports in the downloads folder."""
    folder = cfg.downloads
    if not folder.is_dir():
        return []
    now = now or time.time()
    state = load_state()
    out = []
    for p in sorted(folder.iterdir()):
        if not (p.is_file() and p.name.startswith("conversations") and p.suffix in (".zip", ".json")):
            continue
        if p.with_name(p.name + ".part").exists() or p.with_name(p.name + ".crdownload").exists():
            continue  # Firefox/Chrome still writing it
        try:
            st = p.stat()
        except OSError:
            continue
        if now - st.st_mtime < SETTLE_SECONDS or st.st_size > MAX_JSON_BYTES:
            continue
        h = digest(p)
        if h in state["imported"] or h in state["rejected"]:
            continue
        out.append(p)
    return out


def scan_downloads(cfg: Config) -> list[ImportResult]:
    """Import new exports from the downloads folder, if the user turned that on. Never raises."""
    if not cfg.watch_downloads:
        return []
    results = []
    try:
        paths = pending_downloads(cfg)
    except OSError as exc:
        log.warning("cannot scan %s: %s", cfg.downloads, exc)
        return []
    for p in paths:
        try:
            results.append(import_chats(cfg, p))
        except ExportError as exc:
            log.warning("skipping %s: %s", p.name, exc)
            state = load_state()
            try:
                state["rejected"][digest(p)] = {"name": p.name, "reason": str(exc)[:200]}
                save_state(state)
            except OSError:
                pass
        except (OSError, TimeoutError) as exc:  # retried on the next sync
            log.warning("chat import from %s failed: %s", p.name, exc)
    return results
