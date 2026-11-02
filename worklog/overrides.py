"""Your corrections to what Worklog worked out: project names, moved sessions, folder rules, tags, summary lines.

Each device writes only its own file, `overrides/<device>.json` in the data repo, so syncing never
conflicts. Reading merges every device's file; for each entry the newest change wins (ties go to the
device name, so every computer ends up with the same answer). A value of None removes an earlier
change, which is how "Reset" works. Records are never rewritten: corrections apply when a day is shown,
so each one can be undone.

    names     project key -> title shown on cards ("xoo" -> "Xoikos POS")
    moves     session id -> project key it belongs to
    rules     home-relative folder -> project key, for sessions started there that found no repo
              (per device: the same path can mean different things on two computers)
    projects  project key -> {"name", "kind", "remote"} for projects made with "New project…"
    tags      session id -> tag
    lines     "<day>|<project key>" -> your one-line summary for that day
    stale     "<day>|<project key>" -> when sessions moved in or out (its saved summary is out of date)
    noguess   session id -> True after you answered "No" to "Probably …?"
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

SECTIONS = ("names", "moves", "rules", "projects", "tags", "lines", "stale", "noguess")
_DEVICE_FILE = re.compile(r"^[A-Za-z0-9_-]{1,40}\.json$")


def _rel(device: str) -> str:
    return f"overrides/{device}.json"


@dataclass
class Overrides:
    names: dict[str, str] = field(default_factory=dict)
    moves: dict[str, str] = field(default_factory=dict)
    rules: dict[tuple[str, str], str] = field(default_factory=dict)  # (device, folder) -> key
    projects: dict[str, dict] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    lines: dict[str, str] = field(default_factory=dict)
    stale: dict[str, float] = field(default_factory=dict)
    noguess: dict[str, bool] = field(default_factory=dict)

    def rule_for(self, device: str, folder: str | None) -> str | None:
        """The project a folder rule sends sessions to: the rule for the folder itself or the closest parent."""
        if not folder:
            return None
        best, best_len = None, -1
        for (dev, f), key in self.rules.items():
            if dev == device and (folder == f or folder.startswith(f.rstrip("/") + "/")) and len(f) > best_len:
                best, best_len = key, len(f)
        return best


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(store_path: Path) -> Overrides:
    """Every device's corrections, merged (newest change wins)."""
    best: dict[str, dict[str, tuple[float, str, object]]] = {s: {} for s in SECTIONS}
    base = store_path / "overrides"
    for f in sorted(base.glob("*.json")) if base.is_dir() else []:
        if not _DEVICE_FILE.match(f.name):
            continue
        device, data = f.stem, _read(f)
        for section in SECTIONS:
            entries = data.get(section)
            for k, entry in (entries.items() if isinstance(entries, dict) else []):
                if not isinstance(entry, dict) or "v" not in entry:
                    continue
                at = float(entry.get("at") or 0)
                ident = f"{device}\x00{k}" if section == "rules" else k
                old = best[section].get(ident)
                if old is None or (at, device) > (old[0], old[1]):
                    best[section][ident] = (at, device, entry["v"])
    ov = Overrides()
    for section in SECTIONS:
        for ident, (_, _, v) in best[section].items():
            if v is None:
                continue  # removed again
            if section == "rules":
                device, folder = ident.split("\x00", 1)
                if isinstance(v, str):
                    ov.rules[(device, folder)] = v
            elif section == "projects":
                if isinstance(v, dict):
                    ov.projects[ident] = v
            elif section == "stale":
                ov.stale[ident] = float(v) if isinstance(v, (int, float)) else 0.0
            elif section == "noguess":
                ov.noguess[ident] = bool(v)
            elif isinstance(v, str):
                getattr(ov, section)[ident] = v
    return ov


def change(store, device: str, edits: dict[str, dict], now: float | None = None) -> None:
    """Write `edits` ({section: {id: value or None}}) to this device's file. Call with the store locked."""
    now = now or time.time()
    path = store.path / _rel(device)
    data = _read(path)
    for section, items in edits.items():
        if section not in SECTIONS:
            raise ValueError(f"unknown section {section}")
        bucket = data.setdefault(section, {})
        for k, v in items.items():
            bucket[k] = {"v": v, "at": now}
    store.write_json(_rel(device), data)
