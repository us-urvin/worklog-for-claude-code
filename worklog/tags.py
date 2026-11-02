"""What kind of work a session was: a small set of tags worked out from what's already in its record.

The rules are quick and need no network. When summaries are written, Claude suggests a tag for the
whole project-day too, and you can always change it on the card (stored in overrides.tags).
"""
from __future__ import annotations

import re
from collections import Counter

TAGS = ("Feature", "Bug fix", "Review", "Refactor", "Docs", "Tests", "Research", "Planning", "Release", "Chat")

_REVIEW = re.compile(r"(^/(code-)?review\b|\breview(ing)?\s+(the\s+)?(pr|pull request|this pr|changes|diff)\b"
                     r"|\bpr\s*#?\d+\b.*\breview|\bcode review\b)", re.I)
_FIX = re.compile(r"\b(fix|fixes|fixed|bug|error|errors|failing|fails|failed|crash|broken|exception|traceback|"
                  r"not working|doesn'?t work|regression)\b", re.I)
_REFACTOR = re.compile(r"\b(refactor|clean ?up|simplif(y|ied)|rename|restructure|tidy)\b", re.I)
_RELEASE = re.compile(r"\b(release|deploy|publish|tag v?\d|version bump|bump (the )?version|ship it|changelog)\b",
                      re.I)
_PLAN = re.compile(r"\b(plan|design|architecture|approach|proposal|spec|roadmap)\b", re.I)
_TESTS = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(_test|\.test|\.spec|_spec)\.\w+$|(^|/)test_[^/]+$", re.I)
_DOCS = re.compile(r"\.(md|mdx|rst|txt|adoc)$|(^|/)docs?/", re.I)


def tag_session(record: dict) -> str:
    """The best-fitting tag for one session record."""
    if record.get("source") == "claude.ai":
        return "Chat"
    prompts = [p for p in record.get("prompts") or [] if isinstance(p, str)]
    files = [f for f in record.get("files_touched") or [] if isinstance(f, str)]
    text = "\n".join(prompts)
    first = prompts[0].lstrip() if prompts else ""
    if _REVIEW.search(first) or sum(bool(_REVIEW.search(p)) for p in prompts) >= max(1, len(prompts) // 3):
        return "Review"
    if files and all(_DOCS.search(f) for f in files):
        return "Docs"
    if files and sum(bool(_TESTS.search(f)) for f in files) / len(files) >= 0.6:
        return "Tests"
    if _RELEASE.search(first) or (prompts and sum(bool(_RELEASE.search(p)) for p in prompts) >= 2):
        return "Release"
    if _FIX.search(text) and (files or record.get("commits")):
        return "Bug fix"
    if _REFACTOR.search(first):
        return "Refactor"
    if not files:
        if _PLAN.search(text):
            return "Planning"
        if _FIX.search(text):
            return "Bug fix"
        return "Research"
    return "Feature"


def top_tag(weights: Counter) -> str | None:
    """The tag with the most time (ties: the order of TAGS)."""
    if not weights:
        return None
    return max(weights, key=lambda t: (weights[t], -TAGS.index(t) if t in TAGS else -99))
