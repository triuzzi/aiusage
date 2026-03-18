from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import tempfile
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


@dataclass
class TeamMember:
    email: str
    spend: float
    lines_accepted: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODEL_SHORT: dict[str, str] = {
    "opus-4-6": "op4.6",
    "opus-4-5": "op4.5",
    "sonnet-4-6": "sn4.6",
    "sonnet-4-5": "sn4.5",
    "haiku-4-5": "hk4.5",
    "sonnet-3-5": "sn3.5",
    "haiku-3-5": "hk3.5",
}


def short_model(name: str) -> str:
    for k, v in MODEL_SHORT.items():
        if k in name:
            return v
    return name[:5]


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
# ccusage CLI
# ---------------------------------------------------------------------------

def fetch_ccusage(since_days: int = 60) -> list[DailyUsage]:
    since = (datetime.now() - timedelta(days=since_days)).strftime("%Y%m%d")
    try:
        result = subprocess.run(
            ["ccusage", "daily", "--since", since, "--json", "--breakdown"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.warning("ccusage failed: %s", result.stderr[:200])
            return []
        raw = json.loads(result.stdout)
    except Exception:
        log.exception("ccusage error")
        return []

    days: list[DailyUsage] = []
    for d in raw.get("daily", []):
        models: dict[str, ModelUsage] = {}
        for mb in d.get("modelBreakdowns", []):
            tokens = (
                mb.get("inputTokens", 0) + mb.get("outputTokens", 0)
                + mb.get("cacheCreationTokens", 0) + mb.get("cacheReadTokens", 0)
            )
            models[mb["modelName"]] = ModelUsage(name=mb["modelName"], tokens=tokens, cost=mb.get("cost", 0))
        days.append(DailyUsage(
            date=d["date"],
            total_tokens=d.get("totalTokens", 0),
            total_cost=d.get("totalCost", 0),
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


# ---------------------------------------------------------------------------
# Brave cookie extraction (macOS)
# ---------------------------------------------------------------------------

def _get_brave_aes_key() -> bytes | None:
    """Derive AES key from Brave's Keychain-stored Safe Storage password."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: F401
    except ImportError:
        log.warning("cryptography not installed — platform API disabled")
        return None

    try:
        key_bytes = subprocess.check_output(
            ["security", "find-generic-password", "-w", "-s", "Brave Safe Storage", "-a", "Brave"],
            stderr=subprocess.DEVNULL,
        ).strip()
        return hashlib.pbkdf2_hmac("sha1", key_bytes, b"saltysalt", 1003, dklen=16)
    except Exception:
        log.exception("Failed to get Brave encryption key")
        return None


def _decrypt_cookie(encrypted: bytes, aes_key: bytes) -> str | None:
    """Decrypt a single Brave cookie value (v10 AES-128-CBC)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if not encrypted or encrypted[:3] != b"v10":
        return encrypted.decode("utf-8", errors="replace") if encrypted else None

    ct = encrypted[3:]
    iv = b" " * 16
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    plain = decryptor.update(ct) + decryptor.finalize()

    # Strip PKCS7 padding
    pad = plain[-1]
    if 0 < pad <= 16 and all(b == pad for b in plain[-pad:]):
        plain = plain[:-pad]

    # First 2 AES blocks (32 bytes) are garbage due to CBC IV;
    # try known token patterns first, then fall back to byte-32 onward.
    for pattern in (rb"sk-ant-\S+", rb"[0-9a-f]{8}-[0-9a-f]{4}-"):
        match = re.search(pattern, plain)
        if match:
            # For UUIDs, capture the full UUID
            if b"-" in match.group(0):
                uuid_match = re.search(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", plain)
                if uuid_match:
                    return uuid_match.group(0).decode("ascii")
            else:
                return match.group(0).decode("ascii")

    # Fallback: skip first 2 blocks, take printable ASCII
    chunk = plain[32:]
    try:
        return chunk.decode("utf-8").strip("\x00")
    except UnicodeDecodeError:
        # Extract longest printable ASCII run
        m = re.search(rb"[\x20-\x7e]{4,}", chunk)
        return m.group(0).decode("ascii") if m else None


def _get_brave_cookies(hosts: list[str], names: list[str] | None = None) -> dict[str, str]:
    """Extract cookies for given hosts from Brave. Returns {name: value} dict."""
    aes_key = _get_brave_aes_key()
    if not aes_key:
        return {}

    cookie_db = Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies"
    if not cookie_db.exists():
        log.warning("Brave cookie DB not found")
        return {}

    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy2(cookie_db, tmp)

    try:
        conn = sqlite3.connect(tmp)
        placeholders = ",".join("?" * len(hosts))
        query = f"SELECT name, encrypted_value, host_key FROM cookies WHERE host_key IN ({placeholders})"
        if names:
            name_ph = ",".join("?" * len(names))
            query += f" AND name IN ({name_ph})"
            rows = conn.execute(query, hosts + names).fetchall()
        else:
            rows = conn.execute(query, hosts).fetchall()
        conn.close()
    finally:
        Path(tmp).unlink(missing_ok=True)

    cookies: dict[str, str] = {}
    for name, enc, host in rows:
        val = _decrypt_cookie(enc, aes_key)
        if val:
            cookies[name] = val

    return cookies


_COOKIE_HOSTS = [
    ".platform.claude.com", "platform.claude.com",
    ".claude.com", "claude.com",
]


def _build_cookie_jar() -> tuple[dict[str, str], str]:
    """Extract all relevant cookies and build Cookie header string."""
    cookies = _get_brave_cookies(_COOKIE_HOSTS)
    header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return cookies, header


# ---------------------------------------------------------------------------
# Platform API
# ---------------------------------------------------------------------------

PLATFORM_BASE = "https://platform.claude.com"

_PLATFORM_HEADERS = {
    "Accept": "*/*",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
    "anthropic-client-platform": "web_console",
    "anthropic-client-version": "unknown",
    "Content-Type": "application/json",
    "Referer": "https://platform.claude.com/claude-code",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


def _platform_get(path: str, cookie_header: str) -> dict | None:
    url = f"{PLATFORM_BASE}{path}"
    headers = {**_PLATFORM_HEADERS, "Cookie": cookie_header}
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception:
        log.exception("Platform API failed: %s", path)
        return None


def get_org_uuid(cookie_header: str) -> str | None:
    """Resolve active organization UUID via bootstrap."""
    data = _platform_get(
        "/api/bootstrap?statsig_hashing_algorithm=djb2&growthbook_format=sdk",
        cookie_header,
    )
    if not data:
        return None

    account = data.get("account")
    if not account:
        return None

    # Try memberships → organization → uuid
    memberships = account.get("memberships", [])
    if memberships:
        org = memberships[0].get("organization", {})
        if org.get("uuid"):
            return org["uuid"]

    # Fallback: direct fields
    for key in ("active_organization_uuid", "organization_uuid"):
        if account.get(key):
            return account[key]

    # Fallback: organizations list
    orgs = account.get("organizations", [])
    if orgs:
        return orgs[0].get("uuid")

    log.warning("Could not resolve org UUID from bootstrap: keys=%s", list(account.keys()))
    return None


def fetch_team(cookie_header: str, org_uuid: str, start_date: str, end_date: str) -> list[TeamMember]:
    """Fetch all team members' usage (paginated)."""
    members: list[TeamMember] = []
    offset = 0
    limit = 50

    while True:
        path = (
            f"/api/claude_code/metrics_aggs/users?"
            f"start_date={start_date}&end_date={end_date}"
            f"&limit={limit}&offset={offset}"
            f"&sort_by=total_cost_usd&sort_order=desc"
            f"&organization_uuid={org_uuid}"
        )
        data = _platform_get(path, cookie_header)
        if not data:
            break

        users = data.get("users", [])
        if not users:
            break

        for u in users:
            email = u.get("email") or f"{u.get('api_key_name', 'API key')} [API]"
            try:
                spend = float(u.get("total_cost", 0) or 0)
            except (ValueError, TypeError):
                spend = 0.0
            members.append(TeamMember(
                email=email,
                spend=spend,
                lines_accepted=u.get("total_lines_accepted", 0) or 0,
            ))

        pagination = data.get("pagination", {})
        if not pagination.get("has_next", False):
            break
        offset += limit

    return members


class PlatformClient:
    """Lazy-init wrapper around the platform API."""

    def __init__(self) -> None:
        self._cookie_header: str | None = None
        self.org_uuid: str | None = None
        self._initialized = False
        self.error: str | None = None
        self.connected = False

    def init(self) -> bool:
        """Try to authenticate. Returns True if ready."""
        if self._initialized:
            return self.connected

        self._initialized = True
        cookies, cookie_header = _build_cookie_jar()
        if "sessionKey" not in cookies:
            self.error = "No session cookie — open platform.claude.com in Brave first"
            return False

        self._cookie_header = cookie_header

        self.org_uuid = get_org_uuid(cookie_header)
        if not self.org_uuid:
            self.error = "Could not resolve organization — check platform.claude.com login"
            return False

        self.error = None
        self.connected = True
        return True

    def fetch_team(self, start_date: str, end_date: str) -> list[TeamMember]:
        if not self._cookie_header or not self.org_uuid:
            return []
        return fetch_team(self._cookie_header, self.org_uuid, start_date, end_date)
