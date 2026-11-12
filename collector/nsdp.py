"""NCSI National Summary Data Page (IMF e-GDDS): 20 SDMX files, one per data category."""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import date

from common import http_get, iso, log, now_utc, settings


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def period_end(p: str) -> date | None:
    m = re.match(r"^(\d{4})(?:-?([QMS])?(\d{1,2}))?", (p or "").strip().upper())
    if not m:
        return None
    y, kind, n = int(m.group(1)), m.group(2), m.group(3)
    if n is None:
        mo = 12
    elif kind == "Q":
        mo = 3 * int(n)
    elif kind == "S":
        mo = 6 * int(n)
    else:
        mo = int(n)
    if not 1 <= mo <= 12:
        return None
    nxt = date(y + (mo == 12), 1 if mo == 12 else mo + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def summarize(xml: bytes) -> dict:
    root = ET.fromstring(xml)
    prepared, series, obs, freqs = "", 0, 0, set()
    latest_by_freq: dict[str, tuple] = {}
    first = None
    for s in root.iter():
        name = _local(s.tag)
        if name == "Prepared" and s.text:
            prepared = s.text.strip()
        if name != "Series":
            continue
        series += 1
        f = s.get("FREQ") or s.get("FREQUENCY")
        for el in s.iter():
            if _local(el.tag) == "Value" and el.get("id") in ("FREQ", "FREQUENCY"):
                f = el.get("value")
        f = f or "?"
        freqs.add(f)
        for o in s.iter():
            if _local(o.tag) != "Obs":
                continue
            obs += 1
            p = o.get("TIME_PERIOD")
            if not p:
                for c in o:
                    if _local(c.tag) in ("ObsDimension", "Time"):
                        p = c.get("value") or c.text
                        break
            pe = period_end(p)
            if pe is None:
                continue
            if first is None or pe < first[0]:
                first = (pe, p)
            if f not in latest_by_freq or pe > latest_by_freq[f][0]:
                latest_by_freq[f] = (pe, p)
    latest = max(latest_by_freq.values(), default=None)
    # main frequency = the most frequent one available (M before Q before A)
    main = next((x for x in ("M", "Q", "A") if x in latest_by_freq), next(iter(latest_by_freq), None))
    return {
        "series": series, "observations": obs, "frequencies": sorted(freqs),
        "first_period": first[1] if first else None,
        "latest_period": latest[1] if latest else None,
        "latest_period_end": latest[0].isoformat() if latest else None,
        "main_frequency": main,
        "latest_by_frequency": {k: v[1] for k, v in latest_by_freq.items()},
        "prepared": prepared,
    }


LIMITS = {"M": 4, "Q": 7, "S": 12, "A": 18}  # months of lag still counted as fresh


def freshness(entry: dict) -> str:
    """Fresh if any frequency in the file is within its normal publication lag."""
    if entry.get("status") != "ok" or not entry.get("latest_by_frequency"):
        return "unavailable"
    today = date.today()
    best = None
    for f, p in entry["latest_by_frequency"].items():
        end = period_end(p)
        if end is None:
            continue
        lag = (today.year - end.year) * 12 + today.month - end.month
        ratio = lag / LIMITS.get(f, 18)
        if best is None or ratio < best[0]:
            best = (ratio, lag, f, p)
    if best is None:
        return "unavailable"
    entry["lag_months"], entry["main_frequency"], entry["latest_period"] = best[1], best[2], best[3]
    return "fresh" if best[0] <= 1 else "stale"


def check(state: dict, baseline: bool) -> list[dict]:
    cfg = settings()
    base = cfg.get("nsdp_base", "https://nsdp.ncsi.gov.om/")
    cats = state.setdefault("nsdp", {})
    events = []
    now = iso(now_utc())
    for item in cfg.get("nsdp_files", []):
        key = item["key"]
        prev = cats.get(key, {})
        entry = {"key": key, "ar": item["ar"], "en": item["en"], "file": item["file"], "checked_at": now,
                 "excel_url": base + ".%5C" + re.sub(r"\.xml$", ".xlsx", item["file"], flags=re.I)}
        body, used = None, None
        for url in (base + ".%5C" + item["file"], base + item["file"]):
            r = http_get(url, timeout=90)
            if r is not None and r.status_code == 200 and r.content.lstrip()[:1] == b"<":
                body, used = r.content, url
                break
        if body is None:
            entry["status"] = "unavailable"
        else:
            entry["url"] = used
            entry["sha256"] = hashlib.sha256(body).hexdigest()
            try:
                entry.update(summarize(body))
                entry["status"] = "ok"
            except ET.ParseError:
                entry["status"] = "invalid"
        entry["freshness"] = freshness(entry)
        entry["last_changed"] = prev.get("last_changed")
        entry["history"] = prev.get("history", [])
        if entry.get("sha256") and entry.get("sha256") != prev.get("sha256"):
            entry["last_changed"] = now
            entry["history"] = (entry["history"] + [{"at": now, "latest_period": entry.get("latest_period"),
                                                     "sha256": entry["sha256"][:12]}])[-30:]
            if prev.get("sha256") and not baseline:
                new_period = entry.get("latest_period") != prev.get("latest_period")
                events.append({"at": now, "type": "nsdp_new_period" if new_period else "nsdp_update", "id": f"nsdp-{key}",
                               "title_ar": item["ar"], "title_en": item["en"], "url": used,
                               "detail": {"from": prev.get("latest_period"), "to": entry.get("latest_period")}})
        cats[key] = entry
    ok = sum(1 for e in cats.values() if e.get("status") == "ok")
    log(f"nsdp: {ok}/{len(cats)} categories readable")
    return events
