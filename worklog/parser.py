"""Parse Claude Code JSONL transcripts into per-session, per-day aggregates.

Transcripts live at <config_dir>/projects/<encoded-path>/<session-id>.jsonl.
Their format is NOT a documented stable API, so parsing is defensive:
malformed or unknown lines are skipped and counted, never fatal.
"""
from __future__ import annotations

import json
import logging
import re
import shlex
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
# User entries that are system/CLI noise rather than real prompts.
_SKIP_PREFIXES = ("<command-", "<local-command", "<system-reminder", "Caveat:", "[Request interrupted")
# Messages Claude Code itself inserts as "user" turns are wrapped in a hyphenated tag: <task-notification>,
# <bash-input>, <bash-stdout>, <user-prompt-submit-hook>, ... The tag is followed by a new line or has its
# closing tag, so a prompt you type such as "<my-button> doesn't render" or "<div> is broken" still counts.
_TAG_START = re.compile(r"^<([a-z][a-z0-9_]*-[a-z0-9_-]+)>")
# Gaps between messages longer than this are breaks, not work (for active time).
ACTIVE_GAP = timedelta(minutes=15)
READ_TOOLS = {"Read", "Grep", "Glob", "LS"}
_TASK_DONE = {"completed", "done"}
_TASK_CREATED = re.compile(r"Task #(\w+) created")


# PR and ticket numbers: "#505", "PR 505", ".../pull/505", "gh pr view 505", and Jira-style "XOO2-295".
_PR_REFS = (re.compile(r"(?<![\w&/])#(\d{1,5})(?![\w-])"),
            re.compile(r"\b(?:PR|pull request|MR|merge request|review)\s*#?(\d{1,6})\b", re.I),
            re.compile(r"/(?:pull|pulls|merge_requests|issues)/(\d{1,6})\b"),
            re.compile(r"\bgh\s+(?:pr|issue)\s+(?:view|checkout|review|merge|comment|diff|close|edit|checks)\s+(\d{1,6})\b"))
_TICKET = re.compile(r"(?<![\w/-])([A-Z][A-Z0-9]{1,9})-(\d{1,6})(?![\w-])")
_BRANCH_TICKET = re.compile(r"(?:^|[/_-])([A-Z][A-Z0-9]{1,9})-(\d{1,6})(?=$|[/_-])")  # fix/XOO2-294-qty
_NOT_TICKETS = set("""UTF SHA ISO RFC PEP GPT AES RSA HTTP TLS SSL COVID CVE IPV ES ECMA WCAG X86 ARM64 MD5 CRC UTC GMT
FIPS IEEE ANSI OWASP TCP UDP CHAPTER STEP PHASE PART TOP LEVEL WIN MAC IOS DDR USB HDMI IPS""".split())
_TEST_CMD = re.compile(r"(?:^|[\s;&|(])(?:pytest|py\.test|python3?\s+-m\s+(?:pytest|unittest)|(?:npm|yarn|pnpm|bun)\s+"
                       r"(?:run\s+)?test(?![\w-])|npx\s+(?:jest|vitest)|jest|vitest|go\s+test|cargo\s+test|phpunit|"
                       r"vendor/bin/phpunit|php\s+artisan\s+test|rspec|mvn\s+(?:-\S+\s+)*test|(?:\./)?gradlew?\s+test|"
                       r"dotnet\s+test|mix\s+test|rails\s+test|tox|nox)(?![\w-])")
_FAIL_COUNT = re.compile(r"(\d+)\s+(?:failed|failing|failures?|errors?)\b", re.I)
_FAIL_WORD = re.compile(r"\bFAIL(?:ED)?\b|\bERRORS?:")
_PASSED = re.compile(r"(\d+)\s+(?:passed|passing)\b|\bOK\b|\bPASS(?:ED)?\b|test result: ok", re.I)


def find_refs(text: str, branch: bool = False) -> list[str]:
    """"#505"-style PR numbers and "XOO2-295"-style tickets mentioned in a prompt, command or branch name."""
    out: list[str] = []
    if branch:
        return [f"{m.group(1)}-{int(m.group(2))}" for m in _BRANCH_TICKET.finditer(text)
                if m.group(1) not in _NOT_TICKETS][:3]
    for rx in _PR_REFS:
        for m in rx.finditer(text):
            ref = f"#{int(m.group(1))}"
            if ref not in out and m.group(1) != "0":
                out.append(ref)
    for m in _TICKET.finditer(text):
        if m.group(1) not in _NOT_TICKETS and not m.group(1).isdigit():
            ref = f"{m.group(1)}-{int(m.group(2))}"
            if ref not in out:
                out.append(ref)
    return out


def run_outcome(text: str, is_error: bool) -> bool | None:
    """True if a test run passed, False if it failed, None if its output doesn't say."""
    counts = [int(m.group(1)) for m in _FAIL_COUNT.finditer(text)]
    if counts:
        if any(counts):
            return False
    elif _FAIL_WORD.search(text) or is_error:
        return False
    return True if _PASSED.search(text) else None


def is_noise(text: str) -> bool:
    """True for system/CLI text that shows up as a user turn but isn't something you typed."""
    t = text.strip()
    if not t or t.startswith(_SKIP_PREFIXES):
        return True
    m = _TAG_START.match(t)
    return bool(m) and (t[m.end():m.end() + 1] in ("\n", "") or f"</{m.group(1)}>" in t)
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
    # Earliest timestamp seen per session in any file, on any day: when the session really started.
    session_first: dict[str, datetime] = field(default_factory=dict)
    # Tasks created on earlier days, so one finished today is still known (per session, by task id).
    tasks_before: dict[str, dict[str, dict]] = field(default_factory=dict)
    pending_tasks: dict[str, tuple[str, str]] = field(default_factory=dict)  # tool_use_id -> (session, text)
    pending_tests: dict[str, str] = field(default_factory=dict)  # tool_use_id of a test command -> session


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
    subagent_tokens_by_model: dict[str, dict[str, int]] = field(default_factory=dict)  # part of tokens_by_model
    api_calls: int = 0
    prompts: list[str] = field(default_factory=list)
    files: set[str] = field(default_factory=set)
    tool_calls: Counter = field(default_factory=Counter)
    stamps: list[datetime] = field(default_factory=list)  # every entry's time, for active time
    # Folders the session worked in without starting there (cd / git -C in commands, files read),
    # and repos named on the command line (gh -R owner/repo). Only used to find the project.
    work_dirs: Counter = field(default_factory=Counter)
    repo_slugs: Counter = field(default_factory=Counter)
    refs: list[str] = field(default_factory=list)  # PR and ticket numbers
    tests: list[bool | None] = field(default_factory=list)  # each test run's outcome, in order
    turns: list[tuple[datetime, bool]] = field(default_factory=list)  # (time, is it your prompt) for Claude's time
    todos: list[dict] | None = None  # the last TodoWrite list of the day
    tasks: dict[str, dict] = field(default_factory=dict)  # TaskCreate/TaskUpdate tasks by id
    tasks_touched: set[str] = field(default_factory=set)

    def active_minutes(self) -> int:
        """Minutes with messages no more than ACTIVE_GAP apart: time spent working, not time the window was open."""
        ts = sorted(self.stamps)
        total = sum((b - a for a, b in zip(ts, ts[1:], strict=False) if b - a <= ACTIVE_GAP), timedelta())
        return round(total.total_seconds() / 60)

    def claude_minutes(self) -> int:
        """Minutes Claude spent answering: from each of your prompts to its last step before your next one
        (gaps over ACTIVE_GAP don't count, like active time)."""
        total, prev, busy = timedelta(), None, False
        for ts, mine in sorted(self.turns):
            if mine:
                busy, prev = True, ts
                continue
            if busy and prev is not None and ts - prev <= ACTIVE_GAP:
                total += ts - prev
            prev = ts
        return round(total.total_seconds() / 60)

    def test_counts(self) -> dict[str, int]:
        """{"runs", "failed", "fixed"}: a fix is a passing run after a failing one."""
        runs, failed, fixed, red = len(self.tests), 0, 0, False
        for ok in self.tests:
            if ok is False:
                failed += 1
                red = True
            elif ok is True and red:
                fixed += 1
                red = False
        return {"runs": runs, "failed": failed, "fixed": fixed} if runs else {}

    def add_refs(self, text: str, branch: bool = False) -> None:
        for ref in find_refs(text, branch):
            if ref not in self.refs and len(self.refs) < 20:
                self.refs.append(ref)

    def task_list(self) -> list[dict]:
        """Claude Code's to-do items for this day: {"text", "status": "done"|"open"}."""
        out: list[dict] = []
        if self.todos is not None:
            out += [{"text": t["text"], "status": t["status"]} for t in self.todos]
        for tid, t in self.tasks.items():
            if t.get("deleted"):
                continue
            if tid in self.tasks_touched or t["status"] == "open":  # done earlier: not part of today
                out.append({"text": t["text"], "status": t["status"]})
        seen, unique = set(), []
        for t in out:
            if t["text"] and t["text"] not in seen:
                seen.add(t["text"])
                unique.append(t)
        return unique

    def touch(self, ts: datetime) -> None:
        self.first = ts if self.first is None or ts < self.first else self.first
        self.last = ts if self.last is None or ts > self.last else self.last

    def add_usage(self, model: str, usage: dict, subagent: bool = False) -> None:
        per_model = self.tokens_by_model.setdefault(model, zero_tokens())
        sub = self.subagent_tokens_by_model.setdefault(model, zero_tokens()) if subagent else None
        for src, dst in _USAGE_FIELDS.items():
            v = usage.get(src)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                self.tokens[dst] += v
                per_model[dst] += v
                if sub is not None:
                    sub[dst] += v
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
    if is_noise(text):
        return None
    return text


def _status(raw: object) -> str:
    return "done" if str(raw).lower() in _TASK_DONE else "open"


def _task_events(entry: dict, sid: str, tasks: dict[str, dict], stats: ParseStats,
                 touched: set[str] | None, todos_out: list | None = None) -> list[dict] | None:
    """Apply to-do tool calls and results in `entry` to `tasks`. Returns a TodoWrite list if there is one."""
    if entry.get("isSidechain"):
        return None  # sub-agents keep their own lists
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    todos = None
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            name, inp = block.get("name"), block.get("input") if isinstance(block.get("input"), dict) else {}
            if name == "TodoWrite" and isinstance(inp.get("todos"), list):
                todos = [{"text": str(t.get("content") or t.get("subject") or "").strip(),
                          "status": _status(t.get("status"))} for t in inp["todos"] if isinstance(t, dict)]
            elif name == "TaskCreate" and isinstance(inp.get("subject"), str):
                stats.pending_tasks[str(block.get("id"))] = (sid, inp["subject"].strip())
            elif name == "TaskUpdate" and inp.get("taskId") is not None:
                tid = str(inp["taskId"]).lstrip("#")
                t = tasks.get(tid)
                if t is None:
                    continue
                if inp.get("status") == "deleted":
                    t["deleted"] = True
                elif inp.get("status"):
                    t["status"] = _status(inp["status"])
                if isinstance(inp.get("subject"), str) and inp["subject"].strip():
                    t["text"] = inp["subject"].strip()
                if touched is not None:
                    touched.add(tid)
        elif block.get("type") == "tool_result":
            pending = stats.pending_tasks.pop(str(block.get("tool_use_id")), None)
            if pending is None:
                continue
            raw = block.get("content")
            text = raw if isinstance(raw, str) else " ".join(
                b.get("text", "") for b in raw if isinstance(b, dict)) if isinstance(raw, list) else ""
            m = _TASK_CREATED.search(text)
            if m:
                tasks[m.group(1)] = {"text": pending[1], "status": "open"}
                if touched is not None:
                    touched.add(m.group(1))
    return todos


def _test_results(entry: dict, sd: SessionDay, stats: ParseStats) -> None:
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        if stats.pending_tests.pop(str(block.get("tool_use_id")), None) is None:
            continue
        raw = block.get("content")
        text = raw if isinstance(raw, str) else " ".join(
            b.get("text", "") for b in raw if isinstance(b, dict)) if isinstance(raw, list) else ""
        sd.tests.append(run_outcome(text[-20_000:], block.get("is_error") is True))


_CD = re.compile(r"(?:^|[;&|(]\s*|\bthen\s+|\bdo\s+)cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|)]+)")
_GIT_C = re.compile(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|[^\s;&|)]+)")
_REPO_FLAG = re.compile(r"(?:\s-R|\s--repo)(?:\s+|=)([\w.-]+/[\w.-]+)")


def _unquote(v: str) -> str:
    try:
        parts = shlex.split(v)
    except ValueError:
        return v.strip("'\"")
    return parts[0] if parts else ""


def command_places(command: str, cwd: str | None) -> tuple[list[str], list[str]]:
    """Folders a shell command works in (cd X, git -C X) and repos it names (gh -R owner/repo)."""
    dirs = []
    for m in list(_CD.finditer(command)) + list(_GIT_C.finditer(command)):
        raw = _unquote(m.group(1))
        if not raw or raw in ("-", "..", ".") or "$" in raw:
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            if not cwd:
                continue
            p = Path(cwd) / p
        dirs.append(str(p))
    return dirs, [m.group(1) for m in _REPO_FLAG.finditer(command)]


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
        if ts is None:
            continue
        first_sid = str(entry.get("sessionId") or path.stem)
        if first_sid not in stats.session_first or ts < stats.session_first[first_sid]:
            stats.session_first[first_sid] = ts
        if ts.date() < day:  # remember tasks made earlier, so finishing one today is recorded
            _task_events(entry, first_sid, stats.tasks_before.setdefault(first_sid, {}), stats, None)
            continue
        if ts.date() != day:
            continue
        uuid = entry.get("uuid")
        if isinstance(uuid, str):
            if uuid in seen_uuids:
                continue
            seen_uuids.add(uuid)

        sid = str(entry.get("sessionId") or path.stem)
        if sid not in sessions:
            sessions[sid] = SessionDay(sid, day)
            sessions[sid].tasks = {k: dict(v) for k, v in stats.tasks_before.get(sid, {}).items()}
        sd = sessions[sid]
        sd.touch(ts)
        sd.stamps.append(ts)
        cwd = entry.get("cwd") if isinstance(entry.get("cwd"), str) else None
        if cwd:
            sd.cwd_counts[cwd] += 1
        if isinstance(entry.get("gitBranch"), str) and entry["gitBranch"]:
            if entry["gitBranch"] not in sd.branches:
                sd.add_refs(entry["gitBranch"], branch=True)
            sd.branches.add(entry["gitBranch"])

        etype = entry.get("type")
        if etype in ("user", "assistant"):
            todos = _task_events(entry, sid, sd.tasks, stats, sd.tasks_touched)
            if todos is not None:
                sd.todos = todos
        if etype == "user":
            prompt = _user_prompt(entry)
            if prompt:
                sd.prompts.append(prompt)
                sd.turns.append((ts, True))
                sd.add_refs(prompt)
            else:
                sd.turns.append((ts, False))  # tool results: Claude is still working
                _test_results(entry, sd, stats)
        elif etype == "assistant":
            sd.turns.append((ts, False))
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
                    elif name in READ_TOOLS:
                        where = fp or inp.get("path")
                        if isinstance(where, str) and where:
                            sd.work_dirs[str(Path(cwd or "", where).parent if fp else Path(cwd or "", where))] += 1
                    elif name == "Bash" and isinstance(inp.get("command"), str):
                        command = inp["command"][:4000]
                        sd.add_refs(command)
                        if not entry.get("isSidechain") and _TEST_CMD.search(command):
                            stats.pending_tests[str(block.get("id"))] = sid
                        dirs, slugs = command_places(command, cwd)
                        for d in dirs:
                            sd.work_dirs[d] += 3  # an explicit cd counts more than a file read
                        for slug in slugs:
                            sd.repo_slugs[slug.lower()] += 1
            usage = msg.get("usage")
            if isinstance(usage, dict) and model != "<synthetic>":
                key = (msg.get("id"), entry.get("requestId"))
                if key[0] is None or key not in seen_usage:
                    if key[0] is not None:
                        seen_usage.add(key)
                    sd.add_usage(model, usage, subagent=entry.get("isSidechain") is True)
