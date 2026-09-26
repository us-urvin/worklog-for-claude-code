"""Parse Claude Code JSONL transcripts into per-session, per-day aggregates.

Transcripts live at <config_dir>/projects/<encoded-path>/<session-id>.jsonl.
Their format is NOT a documented stable API, so parsing is defensive:
malformed or unknown lines are skipped and counted, never fatal.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
# User entries that are system/CLI noise rather than real prompts.
_SKIP_PREFIXES = ("<command-", "<local-command", "<system-reminder", "Caveat:", "[Request interrupted")
_USAGE_FIELDS = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_creation_input_tokens": "cache_creation",
    "cache_read_input_tokens": "cache_read",
}


def zero_tokens() -> dict[str, int]:
    return {k: 0 for k in _USAGE_FIELDS.values()}


@dataclass
class ParseStats:
    files: int = 0
    lines: int = 0
    bad_lines: int = 0


@dataclass
class SessionDay:
    session_id: str
    day: date
    first: datetime | None = None
    last: datetime | None = None
    cwd_counts: Counter = field(default_factory=Counter)
    branches: set[str] = field(default_factory=set)
    tokens: dict[str, int] = field(default_factory=zero_tokens)
    tokens_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    api_calls: int = 0
    prompts: list[str] = field(default_factory=list)
    files: set[str] = field(default_factory=set)
    tool_calls: Counter = field(default_factory=Counter)

    def touch(self, ts: datetime) -> None:
        self.first = ts if self.first is None or ts < self.first else self.first
        self.last = ts if self.last is None or ts > self.last else self.last

    def add_usage(self, model: str, usage: dict) -> None:
        per_model = self.tokens_by_model.setdefault(model, zero_tokens())
        for src, dst in _USAGE_FIELDS.items():
            v = usage.get(src)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                self.tokens[dst] += v
                per_model[dst] += v
        self.api_calls += 1


def parse_ts(value: object) -> datetime | None:
    """ISO-8601 (UTC 'Z') -> aware datetime in the machine's local timezone."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _entries(path: Path, stats: ParseStats):
    stats.files += 1
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            stats.lines += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                stats.bad_lines += 1  # e.g. a partially written last line
                continue
            if isinstance(obj, dict):
                yield obj


def _user_prompt(entry: dict) -> str | None:
    if entry.get("isMeta") or entry.get("isSidechain"):
        return None  # sidechain = sub-agent prompts written by Claude, not you
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                return None  # tool output fed back to the model, not a prompt
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        text = "\n".join(parts)
    else:
        return None
    text = text.strip()
    if not text or text.startswith(_SKIP_PREFIXES):
        return None
    return text


def parse_transcript(
    path: Path,
    day: date,
    sessions: dict[str, SessionDay],
    seen_usage: set[tuple],
    seen_uuids: set[str],
    stats: ParseStats,
) -> None:
    """Merge entries of `path` dated `day` (local time) into `sessions`.

    `seen_usage` dedups token usage by (message.id, requestId): streamed responses
    write one line per content block, each repeating the same usage.
    `seen_uuids` dedups entries copied into a new file when a session is resumed.
    """
    for entry in _entries(path, stats):
        ts = parse_ts(entry.get("timestamp"))
        if ts is None or ts.date() != day:
            continue
        uuid = entry.get("uuid")
        if isinstance(uuid, str):
            if uuid in seen_uuids:
                continue
            seen_uuids.add(uuid)

        sid = str(entry.get("sessionId") or path.stem)
        sd = sessions.setdefault(sid, SessionDay(sid, day))
        sd.touch(ts)
        if isinstance(entry.get("cwd"), str):
            sd.cwd_counts[entry["cwd"]] += 1
        if isinstance(entry.get("gitBranch"), str) and entry["gitBranch"]:
            sd.branches.add(entry["gitBranch"])

        etype = entry.get("type")
        if etype == "user":
            prompt = _user_prompt(entry)
            if prompt:
                sd.prompts.append(prompt)
        elif etype == "assistant":
            msg = entry.get("message")
            if not isinstance(msg, dict):
                continue
            model = msg.get("model") if isinstance(msg.get("model"), str) else "unknown"
            content = msg.get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name", "unknown"))
                    sd.tool_calls[name] += 1
                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                    fp = inp.get("file_path") or inp.get("notebook_path")
                    if name in EDIT_TOOLS and isinstance(fp, str):
                        sd.files.add(fp)
            usage = msg.get("usage")
            if isinstance(usage, dict) and model != "<synthetic>":
                key = (msg.get("id"), entry.get("requestId"))
                if key[0] is None or key not in seen_usage:
                    if key[0] is not None:
                        seen_usage.add(key)
                    sd.add_usage(model, usage)
