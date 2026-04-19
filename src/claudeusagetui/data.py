from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

log = logging.getLogger("claudeusagetui")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ModelUsage:
    name: str
    tokens: int
    cost: float


@dataclass
class DailyUsage:
    date: str
    total_tokens: int
    total_cost: float
    models: dict[str, ModelUsage] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLAUDE_FAMILY = {"opus": "op", "sonnet": "sn", "haiku": "hk"}


def short_model(name: str) -> str:
    """Auto-generate short model names.

    claude-opus-4-7-20260416  -> op4.7
    claude-sonnet-4-6         -> sn4.6
    claude-haiku-4-5-20251001 -> hk4.5
    gpt-5.4-mini              -> 5.4m
    gpt-5.3-codex             -> 5.3cx
    gpt-5.4                   -> 5.4
    """
    # Claude: claude-{family}-{major}-{minor}[-date]
    for family, prefix in _CLAUDE_FAMILY.items():
        if family in name:
            m = re.search(rf"{family}-(\d+)-(\d+)", name)
            if m:
                return f"{prefix}{m.group(1)}.{m.group(2)}"

    # GPT: gpt-{version}[-suffix]
    m = re.search(r"gpt-(\d+(?:\.\d+)?)-?(.*)", name)
    if m:
        ver = m.group(1)
        suffix = m.group(2)
        tag = ""
        if "mini" in suffix:
            tag = "m"
        elif "codex" in suffix:
            tag = "cx"
        return f"{ver}{tag}"

    return name[:6]


def model_provider(name: str) -> str:
    """Classify a model name as 'claude' or 'codex'."""
    if name.startswith("claude"):
        return "claude"
    return "codex"


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_cost(n: float) -> str:
    return f"${round(n)}"


# ---------------------------------------------------------------------------
# Pricing (per-token, USD) — fetched from LiteLLM at startup, with fallback
# ---------------------------------------------------------------------------

LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

# Fallback pricing when LiteLLM is unreachable (keyed by substring match)
_FALLBACK_PRICING: dict[str, dict[str, float]] = {
    "opus-4-7": {"input": 5e-6, "output": 25e-6, "cache_write": 6.25e-6, "cache_read": 0.5e-6},
    "opus-4-6": {"input": 5e-6, "output": 25e-6, "cache_write": 6.25e-6, "cache_read": 0.5e-6},
    "opus-4-5": {"input": 5e-6, "output": 25e-6, "cache_write": 6.25e-6, "cache_read": 0.5e-6},
    "opus-4-1": {"input": 15e-6, "output": 75e-6, "cache_write": 18.75e-6, "cache_read": 1.5e-6},
    "sonnet-4-6": {"input": 3e-6, "output": 15e-6, "cache_write": 3.75e-6, "cache_read": 0.3e-6},
    "sonnet-4-5": {"input": 3e-6, "output": 15e-6, "cache_write": 3.75e-6, "cache_read": 0.3e-6},
    "haiku-4-5": {"input": 1e-6, "output": 5e-6, "cache_write": 1.25e-6, "cache_read": 0.1e-6},
    "sonnet-3-5": {"input": 3e-6, "output": 15e-6, "cache_write": 3.75e-6, "cache_read": 0.3e-6},
    "haiku-3-5": {"input": 0.8e-6, "output": 4e-6, "cache_write": 1e-6, "cache_read": 0.08e-6},
}

# Populated at startup by _load_pricing()
_PRICING: dict[str, dict[str, float]] = {}


def _load_pricing() -> None:
    """Fetch pricing from LiteLLM and build a lookup table keyed by model name.

    Falls back to hardcoded pricing on failure.
    """
    global _PRICING

    try:
        req = Request(LITELLM_PRICING_URL)
        with urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read())
    except Exception:
        log.warning("Could not fetch LiteLLM pricing — using fallback")
        _PRICING = dict(_FALLBACK_PRICING)
        return

    pricing: dict[str, dict[str, float]] = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            continue
        inp = val.get("input_cost_per_token")
        out = val.get("output_cost_per_token")
        if inp is None or out is None:
            continue
        entry = {
            "input": float(inp),
            "output": float(out),
            "cache_write": float(val.get("cache_creation_input_token_cost") or 0),
            "cache_read": float(val.get("cache_read_input_token_cost") or 0),
        }
        pricing[key] = entry

    if pricing:
        _PRICING = pricing
        log.info("Loaded pricing for %d models from LiteLLM", len(pricing))
    else:
        _PRICING = dict(_FALLBACK_PRICING)


def _compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Compute cost for a single request given token counts."""
    if not _PRICING:
        _load_pricing()

    # Try exact match first (LiteLLM keys like "claude-opus-4-6")
    rates = _PRICING.get(model)
    if not rates:
        # Try substring match (fallback keys like "opus-4-6")
        for pattern, r in _PRICING.items():
            if pattern in model:
                rates = r
                break
    if rates:
        return (
            input_tokens * rates["input"]
            + output_tokens * rates["output"]
            + cache_write_tokens * rates.get("cache_write", 0)
            + cache_read_tokens * rates.get("cache_read", 0)
        )
    # No pricing available (unknown models)
    return 0.0


# ---------------------------------------------------------------------------
# Claude Code JSONL reader (~/.claude/projects/**/*.jsonl)
# ---------------------------------------------------------------------------

CLAUDE_DIR = Path.home() / ".claude" / "projects"


def _read_claude_usage(since: str) -> dict[str, dict[str, ModelUsage]]:
    """Read Claude Code JSONL files and aggregate by date + model.

    Deduplicates by ``message.id`` (same response appears in parent and
    subagent files). Groups by local-timezone date.

    Returns {date: {model: ModelUsage}}.
    """
    result: dict[str, dict[str, ModelUsage]] = {}
    if not CLAUDE_DIR.exists():
        return result

    seen_ids: set[str] = set()
    local_tz = datetime.now().astimezone().tzinfo

    for path in CLAUDE_DIR.rglob("*.jsonl"):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    ts = obj.get("timestamp")
                    msg = obj.get("message")
                    if not ts or not msg:
                        continue

                    model = msg.get("model", "")
                    usage = msg.get("usage")
                    if not model or not usage or model == "<synthetic>":
                        continue

                    # Deduplicate by message.id or requestId
                    msg_id = msg.get("id", "") or obj.get("requestId", "")
                    if msg_id:
                        if msg_id in seen_ids:
                            continue
                        seen_ids.add(msg_id)

                    # Convert UTC timestamp to local date
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        date = dt.astimezone(local_tz).strftime("%Y-%m-%d")
                    except (ValueError, OSError):
                        date = ts[:10]

                    if date < since:
                        continue

                    inp = usage.get("input_tokens", 0)
                    out = usage.get("output_tokens", 0)
                    cw = usage.get("cache_creation_input_tokens", 0)
                    cr = usage.get("cache_read_input_tokens", 0)
                    tokens = inp + out + cw + cr
                    cost = _compute_cost(model, inp, out, cw, cr)

                    day = result.setdefault(date, {})
                    if model in day:
                        day[model] = ModelUsage(
                            name=model,
                            tokens=day[model].tokens + tokens,
                            cost=day[model].cost + cost,
                        )
                    else:
                        day[model] = ModelUsage(name=model, tokens=tokens, cost=cost)
        except Exception:
            log.exception("Error reading %s", path)

    return result


# ---------------------------------------------------------------------------
# Codex rollout reader (~/.codex/sessions/**/*.jsonl)
# ---------------------------------------------------------------------------

CODEX_DIR = Path.home() / ".codex" / "sessions"


def _read_codex_usage(since: str) -> dict[str, dict[str, ModelUsage]]:
    """Read Codex rollout JSONL files and aggregate by date + model.

    Returns {date: {model: ModelUsage}}.
    """
    result: dict[str, dict[str, ModelUsage]] = {}
    if not CODEX_DIR.exists():
        return result

    local_tz = datetime.now().astimezone().tzinfo

    for path in CODEX_DIR.rglob("*.jsonl"):
        try:
            model = ""
            prev_total = 0
            date = ""
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    etype = obj.get("type", "")

                    # Extract model from session_meta or turn_context
                    if etype == "session_meta":
                        payload = obj.get("payload", {})
                        ts = payload.get("timestamp", obj.get("timestamp", ""))
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            date = dt.astimezone(local_tz).strftime("%Y-%m-%d")
                        except (ValueError, OSError):
                            date = ts[:10]
                        if date < since:
                            break  # skip entire file
                    elif etype == "turn_context":
                        payload = obj.get("payload", {})
                        m = payload.get("model", "")
                        if m:
                            model = m

                    # Extract per-turn token delta from token_count events
                    elif etype == "event_msg":
                        payload = obj.get("payload", {})
                        if payload.get("type") != "token_count":
                            continue
                        info = payload.get("info")
                        if not info:
                            continue

                        if not date:
                            ts = obj.get("timestamp", "")
                            try:
                                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                date = dt.astimezone(local_tz).strftime("%Y-%m-%d")
                            except (ValueError, OSError):
                                date = ts[:10]
                            if date < since:
                                break

                        # Use total_token_usage delta to avoid double-counting
                        total = info.get("total_token_usage", {})
                        new_total = total.get("total_tokens", 0)
                        if new_total <= prev_total:
                            continue  # duplicate event
                        prev_total = new_total

                        last = info.get("last_token_usage", {})
                        inp = last.get("input_tokens", 0)
                        out = last.get("output_tokens", 0)
                        cached = last.get("cached_input_tokens", 0)
                        tokens = inp + out
                        cost = _compute_cost(model, inp - cached, out)

                        if not model or not date:
                            continue

                        day = result.setdefault(date, {})
                        if model in day:
                            day[model] = ModelUsage(
                                name=model,
                                tokens=day[model].tokens + tokens,
                                cost=day[model].cost + cost,
                            )
                        else:
                            day[model] = ModelUsage(name=model, tokens=tokens, cost=cost)
        except Exception:
            log.exception("Error reading %s", path)

    return result


# ---------------------------------------------------------------------------
# Unified fetcher
# ---------------------------------------------------------------------------

def fetch_usage(since_days: int = 60) -> list[DailyUsage]:
    """Read usage from both Claude Code and Codex, merged by date."""
    since = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")

    claude = _read_claude_usage(since)
    codex = _read_codex_usage(since)

    # Merge into unified dict
    all_dates: dict[str, dict[str, ModelUsage]] = {}
    for source in (claude, codex):
        for date, models in source.items():
            day = all_dates.setdefault(date, {})
            for name, mu in models.items():
                if name in day:
                    day[name] = ModelUsage(
                        name=name,
                        tokens=day[name].tokens + mu.tokens,
                        cost=day[name].cost + mu.cost,
                    )
                else:
                    day[name] = ModelUsage(name=name, tokens=mu.tokens, cost=mu.cost)

    # Convert to sorted list
    days: list[DailyUsage] = []
    for date in sorted(all_dates):
        models = all_dates[date]
        total_tokens = sum(m.tokens for m in models.values())
        total_cost = sum(m.cost for m in models.values())
        days.append(DailyUsage(
            date=date,
            total_tokens=total_tokens,
            total_cost=total_cost,
            models=models,
        ))

    return days


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def aggregate_usage(
    days: list[DailyUsage], since: str | None = None
) -> tuple[dict[str, ModelUsage], float, int]:
    """Aggregate usage across days, optionally filtering by date >= since.

    Returns (models_dict, total_cost, total_tokens).
    """
    merged: dict[str, ModelUsage] = {}
    total_cost = 0.0
    total_tokens = 0
    for d in days:
        if since and d.date < since:
            continue
        total_cost += d.total_cost
        total_tokens += d.total_tokens
        for name, m in d.models.items():
            if name in merged:
                merged[name] = ModelUsage(name=name, tokens=merged[name].tokens + m.tokens, cost=merged[name].cost + m.cost)
            else:
                merged[name] = ModelUsage(name=name, tokens=m.tokens, cost=m.cost)
    return merged, total_cost, total_tokens


def all_model_names(days: list[DailyUsage]) -> list[str]:
    """Get sorted model names by total cost (descending)."""
    totals: dict[str, float] = {}
    for d in days:
        for name, m in d.models.items():
            totals[name] = totals.get(name, 0) + m.cost
    return sorted(totals, key=lambda n: -totals[n])


def sparkline(days: list[DailyUsage], last_n: int = 30) -> str:
    """Generate a sparkline string for daily costs."""
    blocks = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    costs = [d.total_cost for d in days[-last_n:]]
    if not costs:
        return ""
    peak = max(costs) or 1
    return "".join(blocks[min(8, int(c / peak * 8))] for c in costs)
