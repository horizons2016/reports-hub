"""Attach a "what changed and where" comparison to revision events (runs inside the collector)."""
from __future__ import annotations

import re

import diff
from common import DATA, http_get, log, save_json

DIR = DATA / "diffs"
TYPES = {"revision", "revision_same_url", "new_upload_unverified"}


def _get(url: str | None) -> bytes | None:
    if not url:
        return None
    r = http_get(url, timeout=180)
    return r.content if r is not None and r.status_code == 200 and r.content else None


def attach(events: list[dict]) -> None:
    for e in events:
        prev = e.get("previous")
        if e.get("type") not in TYPES or not prev:
            continue
        new = _get(e.get("url"))
        old = _get(prev.get("archive_url")) or (_get(prev.get("url")) if prev.get("url") != e.get("url") else None)
        if not new or not old:
            e["diff"] = {"summary_ar": "لم تتوفر النسخة السابقة للمقارنة (لم تُؤرشف قبل استبدالها).", "lines": []}
            continue
        kind = e.get("slot") or "pdf_en"
        d = diff.compare(old, new, kind)
        name = re.sub(r"[^\w.-]", "_", f"{e['id']}_{kind}_v{prev['v']}-v{e['version']}") + ".json"
        d.update({"id": e["id"], "slot": kind, "from_v": prev["v"], "to_v": e["version"], "title_ar": e.get("title_ar"),
                  "title_en": e.get("title_en"), "old_url": prev.get("archive_url") or prev.get("url"), "new_url": e.get("url"), "at": e.get("at")})
        save_json(DIR / name, d)
        e["diff"] = {"summary_ar": d.get("summary_ar"), "file": f"diffs/{name}", "changed": d.get("changed_cells", 0),
                     "lines": diff.lines_ar(d)}
        log(f"diff {e['id']} {kind} v{prev['v']}->v{e['version']}: {d.get('summary_ar')}")
