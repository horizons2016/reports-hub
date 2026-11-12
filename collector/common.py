"""Shared helpers: paths, JSON storage, time, HTTP with retries, file fingerprints."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = ROOT / "config"
ARCHIVE_OUT = ROOT / "archive_out"   # files to upload to GitHub Releases (not committed)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 reports-hub/1.0 (+https://github.com/horizons2016/reports-hub)")

FILE_RE = re.compile(r"(?:https?://api\.ncsi\.gov\.om/)?/?uploads/(?:pdfs|xls)/[^\"'\s<>)\\]+?\.(?:pdf|xlsx?)", re.I)
TS_RE = re.compile(r"(\d{10})\.(?:pdf|xlsx?)$", re.I)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_time(s) -> datetime | None:
    """Parse ISO strings like 2026-09-20T10:20:33.000000Z, 2026-09-20 10:20:33, 2026-09-20."""
    if not s:
        return None
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s, timezone.utc)
    s = str(s).strip().replace(" ", "T")
    s = re.sub(r"\.\d+", "", s).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def file_ts(url: str) -> int | None:
    m = TS_RE.search(url or "")
    return int(m.group(1)) if m else None


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def settings() -> dict:
    return load_json(CONFIG / "settings.json", {})


_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "ar,en;q=0.8"})
        _session = s
    return _session


def http_get(url: str, *, timeout: int = 60, tries: int = 3, **kw) -> requests.Response | None:
    for i in range(tries):
        try:
            r = session().get(url, timeout=timeout, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(3 * (i + 1))
                continue
            return r
        except requests.RequestException as e:
            if i == tries - 1:
                log("HTTP failed", url, type(e).__name__, str(e)[:120])
                return None
            time.sleep(3 * (i + 1))
    return None


def fetch_file(url: str, keep_to: Path | None = None) -> dict | None:
    """Download a file and return {sha256, size}; optionally keep a copy for archiving."""
    r = http_get(url, timeout=180)
    if r is None or r.status_code != 200 or not r.content:
        return None
    body = r.content
    if keep_to is not None:
        keep_to.parent.mkdir(parents=True, exist_ok=True)
        keep_to.write_bytes(body)
    return {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}


def days_between(a: datetime | None, b: datetime | None) -> float | None:
    if not a or not b:
        return None
    return round((b - a).total_seconds() / 86400, 2)


__all__ = [n for n in dir() if not n.startswith("_")] + ["timedelta"]
