"""What the tokens would cost at Anthropic's API prices: an estimate for comparison, not a bill.

On Pro and Max plans usage counts against limits rather than being billed per token, so pages say
"≈ $12 at API prices". Prices are per million tokens, from Anthropic's published table (as of
PRICES_AS_OF). Cache writes cost 1.25 × input (5-minute cache) and cache reads 0.1 × input, unless the
table says otherwise. A model that isn't in the table is left out and reported as unknown, never
guessed. Override or add models in the config:

    [cost]
    claude-opus-5-5 = [4.0, 20.0]          # input, output per million tokens
"""
from __future__ import annotations

import re

PRICES_AS_OF = "2026-10-06"

# (family, version) -> (input, output, cache_read or None for 0.1 × input)
_TABLE: dict[tuple[str, str], tuple[float, float, float | None]] = {
    ("fable", "5.1"): (10.0, 50.0, 0.25), ("fable", "5"): (10.0, 50.0, None),
    ("mythos", "5.1"): (10.0, 50.0, 0.25), ("mythos", "5"): (10.0, 50.0, None),
    ("opus", "5.5"): (4.0, 20.0, 0.20), ("opus", "5"): (5.0, 25.0, None),
    ("opus", "4.8"): (5.0, 25.0, None), ("opus", "4.7"): (5.0, 25.0, None), ("opus", "4.6"): (5.0, 25.0, None),
    ("opus", "4.5"): (5.0, 25.0, None), ("opus", "4.1"): (15.0, 75.0, None), ("opus", "4"): (15.0, 75.0, None),
    ("opus", "3"): (15.0, 75.0, None),
    ("sonnet", "5.5"): (2.0, 10.0, 0.20), ("sonnet", "5"): (2.0, 10.0, None), ("sonnet", "4.6"): (3.0, 15.0, None),
    ("sonnet", "4.5"): (3.0, 15.0, None), ("sonnet", "4"): (3.0, 15.0, None), ("sonnet", "3.7"): (3.0, 15.0, None),
    ("sonnet", "3.5"): (3.0, 15.0, None),
    ("haiku", "5.5"): (0.10, 0.50, None), ("haiku", "4.5"): (1.0, 5.0, None), ("haiku", "3.5"): (0.80, 4.0, None),
    ("haiku", "3"): (0.25, 1.25, None),
}
_ID = re.compile(r"^claude-(?:(?P<f1>opus|sonnet|haiku|fable|mythos)-(?P<v1>\d+(?:-\d)?)"
                 r"|(?P<v2>\d+(?:-\d)?)-(?P<f2>opus|sonnet|haiku|fable|mythos))(?:-\d{8})?(?:\[1m\])?$")


def price(model: str, custom: dict[str, tuple[float, float]] | None = None) -> tuple[float, float, float, float] | None:
    """(input, output, cache write, cache read) per million tokens, or None when unknown."""
    if custom and model in custom:
        i, o = custom[model]
        return i, o, i * 1.25, i * 0.1
    m = _ID.match(model or "")
    if not m:
        return None
    family = m["f1"] or m["f2"]
    version = (m["v1"] or m["v2"]).replace("-", ".")
    row = _TABLE.get((family, version))  # an unlisted version stays unknown rather than borrowing a price
    if row is None:
        return None
    i, o, read = row
    return i, o, i * 1.25, read if read is not None else i * 0.1


def estimate(tokens_by_model: dict[str, dict[str, int]], custom=None) -> tuple[float, int]:
    """(dollars, tokens of models without a known price) for {model: {input, output, cache_creation, cache_read}}."""
    total, unknown = 0.0, 0
    for model, t in (tokens_by_model or {}).items():
        if not isinstance(t, dict) or model == "<synthetic>":
            continue
        p = price(model, custom)
        n = sum(v for v in t.values() if isinstance(v, int))
        if p is None:
            unknown += n
            continue
        total += (t.get("input", 0) * p[0] + t.get("output", 0) * p[1] + t.get("cache_creation", 0) * p[2]
                  + t.get("cache_read", 0) * p[3]) / 1_000_000
    return total, unknown


def money(dollars: float) -> str:
    if dollars <= 0:
        return "$0"
    if dollars < 0.01:
        return "< $0.01"
    return f"${dollars:,.2f}" if dollars < 100 else f"${dollars:,.0f}"
