"""Alerts: GitHub Issues (team members who Watch the repo get them by email) and an Atom feed."""
from __future__ import annotations

import os
from xml.sax.saxutils import escape

import requests

from common import DATA, log, settings

SLOT_AR = {"pdf_en": "PDF إنجليزي", "pdf_ar": "PDF عربي", "xls_en": "Excel إنجليزي", "xls_ar": "Excel عربي", "all": "كل الملفات"}
TYPE = {
    "new":                {"ar": "إصدار جديد",                    "label": "إصدار جديد",  "color": "1f6feb", "issue": True},
    "revision":           {"ar": "تعديل: المحتوى تغيّر",          "label": "تعديل",       "color": "d73a49", "issue": True},
    "revision_same_url":  {"ar": "تعديل على نفس الرابط",          "label": "تعديل",       "color": "d73a49", "issue": True},
    "file_added":         {"ar": "ملف أُضيف لإصدار قائم",          "label": "ملف أُضيف",   "color": "0e8a16", "issue": True},
    "nsdp_new_period":    {"ar": "بيانات SDMX: فترة جديدة",       "label": "SDMX",        "color": "8250df", "issue": True},
    "nsdp_update":        {"ar": "بيانات SDMX: تحديث دون فترة جديدة", "label": "SDMX",    "color": "8250df", "issue": False},
    "reupload":           {"ar": "إعادة رفع بنفس المحتوى",        "label": None,          "color": None,     "issue": False},
    "late_older":         {"ar": "نسخة أقدم ظهرت متأخرة",          "label": None,          "color": None,     "issue": False},
    "new_upload_unverified": {"ar": "رفع جديد لم يُتحقق من محتواه", "label": "تعديل",      "color": "d73a49", "issue": True},
}


def type_ar(t: str) -> str:
    return TYPE.get(t, {}).get("ar", t)


def _site_url() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "horizons2016/reports-hub")
    owner, name = repo.split("/")
    return f"https://{owner}.github.io/{name}/"


def _title(e: dict) -> str:
    name = e.get("title_ar") or e.get("title_en") or e.get("id")
    extra = f" — {SLOT_AR.get(e.get('slot'), e.get('slot'))}" if e.get("slot") and e.get("slot") != "all" else ""
    ver = f" (v{e['version']})" if e.get("version") and e.get("type") != "new" else ""
    return f"[{type_ar(e['type'])}] {name}{extra}{ver}"


def _body(e: dict) -> str:
    lines = [f"**{type_ar(e['type'])}**", ""]
    if e.get("title_ar"):
        lines.append(f"- العنوان: {e['title_ar']}")
    if e.get("title_en"):
        lines.append(f"- Title: {e['title_en']}")
    if e.get("slot"):
        lines.append(f"- الملف: {SLOT_AR.get(e['slot'], e['slot'])}")
    if e.get("uploaded"):
        lines.append(f"- وقت الرفع لدى المركز (UTC): {e['uploaded']}")
    if e.get("url"):
        lines.append(f"- الرابط: {e['url']}")
    prev = e.get("previous")
    if prev:
        lines += ["", f"**النسخة السابقة v{prev['v']}** (رُفعت {prev.get('uploaded') or '؟'})", f"- {prev['url']}"]
        if prev.get("archive_url"):
            lines.append(f"- نسخة مؤرشفة: {prev['archive_url']}")
    d = e.get("detail")
    if d:
        lines.append(f"- آخر فترة: {d.get('from') or '—'} ← {d.get('to') or '—'}")
    if e["type"] in ("revision", "revision_same_url"):
        lines += ["", "> الأرقام المأخوذة من النسخة السابقة قد تكون تغيّرت. راجع الجداول المعنية قبل الاقتباس."]
    lines += ["", f"اللوحة: {_site_url()}", "", "_أُنشئ تلقائياً بواسطة مستودع البحوث والتقارير._"]
    return "\n".join(lines)


def _api():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return None, None
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    return s, f"https://api.github.com/repos/{repo}"


def _ensure_labels(s, base):
    for spec in {v["label"]: v["color"] for v in TYPE.values() if v["label"]}.items():
        name, color = spec
        r = s.get(f"{base}/labels/{requests.utils.quote(name)}")
        if r.status_code == 404:
            s.post(f"{base}/labels", json={"name": name, "color": color})


def open_issues(events: list[dict], extra: list[dict] | None = None) -> None:
    """Create one issue per alert-worthy event (capped; a digest beyond the cap)."""
    s, base = _api()
    if not s:
        log("alerts: no GITHUB_TOKEN; skipping issues")
        return
    _ensure_labels(s, base)
    todo = [e for e in events if TYPE.get(e["type"], {}).get("issue")] + (extra or [])
    cap = settings().get("max_issues_per_run", 15)
    single, rest = todo[:cap], todo[cap:]
    for e in single:
        labels = [TYPE[e["type"]]["label"]] if TYPE.get(e["type"], {}).get("label") else []
        r = s.post(f"{base}/issues", json={"title": e.get("issue_title") or _title(e)[:250],
                                           "body": e.get("issue_body") or _body(e), "labels": labels})
        if r.status_code == 201:
            e["issue_url"] = r.json()["html_url"]
        else:
            log("alerts: issue failed", r.status_code, r.text[:200])
    if rest:
        body = "\n".join(f"- {_title(e)}" for e in rest)
        s.post(f"{base}/issues", json={"title": f"[ملخص] {len(rest)} تحديثاً إضافياً في هذا التشغيل",
                                       "body": body + f"\n\nاللوحة: {_site_url()}"})
    log(f"alerts: {len(single)} issues" + (f" + 1 digest ({len(rest)})" if rest else ""))


def write_feed(events: list[dict]) -> None:
    site = _site_url()
    items = []
    for e in list(reversed(events))[:60]:
        link = e.get("issue_url") or e.get("url") or site
        items.append(
            "<entry>"
            f"<title>{escape(_title(e))}</title>"
            f"<link href=\"{escape(link)}\"/>"
            f"<id>{escape(e['id'] + ':' + e['at'] + ':' + str(e.get('slot')) + ':' + e['type'])}</id>"
            f"<updated>{e['at']}</updated>"
            f"<content type=\"text\">{escape(_body(e))}</content>"
            "</entry>")
    updated = events[-1]["at"] if events else "1970-01-01T00:00:00Z"
    xml = ('<?xml version="1.0" encoding="utf-8"?>'
           '<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="ar">'
           f"<title>{escape(settings().get('site_title_ar', 'Reports Hub'))} — التنبيهات</title>"
           f"<link href=\"{site}\"/><id>{site}feed.xml</id><updated>{updated}</updated>"
           + "".join(items) + "</feed>")
    (DATA / "feed.xml").write_text(xml, encoding="utf-8")
