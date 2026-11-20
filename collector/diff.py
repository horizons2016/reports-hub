"""What changed between two versions of the same NCSI file, and where.

PDF:   every page is read line by line. Tables are recognised by their title line ("18 - Expatriates
       Workers", "2 - B : Gross Domestic Product"), rows by their English label (Arabic label as
       fallback). Each row's numbers are compared with the same row in the other version, so a
       changed figure is reported with its table, row, column position and page, even if pages moved.
Excel: cell by cell per sheet (exact), with the row label and sheet name.

Output (data/diffs/<file>.json and a short summary on the event):
  {"kind": "pdf"|"xlsx", "pages_old", "pages_new", "changed_cells", "added_rows", "removed_rows",
   "tables": [{"table", "title", "page", "changes": [{"row", "col", "old", "new", "page"}...]}],
   "text_pages": [pages whose wording changed without number changes], "summary_ar": "..."}
"""
from __future__ import annotations

import io
import re
from collections import defaultdict

NUM = re.compile(r"^[-−–]?\(?\d[\d,]*(?:\.\d+)?\)?%?$")
ARABIC = re.compile(r"[؀-ۿﭐ-﷿ﹰ-﻿]")
LATIN = re.compile(r"[A-Za-z]")
SUP = re.compile(r"[⁽⁾⁰¹²³⁴⁵⁶⁷⁸⁹*]")
TITLE = re.compile(r"^\s*(\d{1,2})\s*[-–]\s*(?:([A-Z])\s*(?::|\s(?=[A-Z])))?\s*:?\s*([A-Za-z].{2,})$")
MAX_REPORTED = 400


def _num(tok: str) -> float | None:
    t = tok.replace("−", "-").replace("–", "-").replace(",", "").replace("%", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _label(tokens: list[str]) -> str:
    """English words of the row; else the Arabic words (pdfplumber returns them reversed)."""
    en = [SUP.sub("", t) for t in tokens if LATIN.search(t) and not ARABIC.search(t)]
    lab = " ".join(t for t in en if t).strip(" -:.")
    if lab:
        return re.sub(r"\s+", " ", lab)
    ar = [SUP.sub("", t)[::-1] for t in reversed(tokens) if ARABIC.search(t)]
    return re.sub(r"\s+", " ", " ".join(ar)).strip(" -:.")


# ---------------------------------------------------------------- PDF
def pdf_rows(data: bytes) -> tuple[dict, dict, int]:
    """Return ({(table, label, n): {"nums": [...], "raw": [...], "page": p}}, {table: title}, pages)."""
    import pdfplumber
    rows, titles, texts = {}, {}, {}
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        npages = len(pdf.pages)
        for pi, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            table = None
            seen = defaultdict(int)
            words_only = []
            for i, line in enumerate(lines):
                m = TITLE.match(line)
                prev = lines[i - 1] if i else ""
                if m and (prev == m.group(1) or not table) and not any(NUM.match(t) for t in line.split()[3:]):
                    table = f"{m.group(1)}{('-' + m.group(2).upper()) if m.group(2) else ''}"
                    titles.setdefault(table, re.sub(r"\s+", " ", m.group(3)).strip(" :"))
                    continue
                toks = line.split()
                nums = [t for t in toks if NUM.match(t)]
                if len(nums) < 1 or len(nums) == len(toks):
                    words_only.append(SUP.sub("", line))
                    continue
                label = _label([t for t in toks if not NUM.match(t)])
                if not label:
                    continue
                key_t = table or f"p{pi}"
                seen[(key_t, label)] += 1
                rows[(key_t, label, seen[(key_t, label)])] = {"nums": [_num(t) for t in nums], "raw": nums, "page": pi}
            texts[pi] = " ".join(words_only)
    return rows, titles, npages, texts


def _fmt(raw: str | None) -> str:
    return raw if raw is not None else "—"


def compare_pdf(old: bytes, new: bytes) -> dict:
    ro, to, po, xo = pdf_rows(old)
    rn, tn, pn, xn = pdf_rows(new)
    titles = {**to, **tn}
    by_table: dict = defaultdict(list)
    changed = added = removed = 0
    for key in sorted(set(ro) | set(rn), key=lambda k: (str(k[0]), k[1], k[2])):
        a, b = ro.get(key), rn.get(key)
        table, label = key[0], key[1]
        if a and b:
            n = max(len(a["nums"]), len(b["nums"]))
            for c in range(n):
                va = a["nums"][c] if c < len(a["nums"]) else None
                vb = b["nums"][c] if c < len(b["nums"]) else None
                if va is None and vb is None:
                    continue
                if va is None or vb is None or abs(va - vb) > 1e-9:
                    changed += 1
                    by_table[table].append({"row": label, "col": c + 1, "old": _fmt(a["raw"][c] if c < len(a["raw"]) else None),
                                            "new": _fmt(b["raw"][c] if c < len(b["raw"]) else None), "page": b["page"], "page_old": a["page"]})
        elif b:
            added += 1
            by_table[table].append({"row": label, "col": None, "old": None, "new": " ".join(b["raw"]), "page": b["page"], "added": True})
        else:
            removed += 1
            by_table[table].append({"row": label, "col": None, "old": " ".join(a["raw"]), "new": None, "page": a["page"], "removed": True})
    tables = []
    for t, ch in by_table.items():
        tables.append({"table": None if t.startswith("p") and t[1:].isdigit() else t, "title": titles.get(t),
                       "page": min(c["page"] for c in ch), "changes": ch})
    tables.sort(key=lambda t: t["page"])
    changed_pages = {c["page"] for t in tables for c in t["changes"]}
    text_pages = [p for p in sorted(set(xo) & set(xn)) if xo[p] != xn[p] and p not in changed_pages]
    return _finish({"kind": "pdf", "pages_old": po, "pages_new": pn, "changed_cells": changed,
                    "added_rows": added, "removed_rows": removed, "tables": tables, "text_pages": text_pages})


# ---------------------------------------------------------------- Excel
def compare_xlsx(old: bytes, new: bytes) -> dict:
    import openpyxl
    from openpyxl.utils import get_column_letter

    def load(b):
        wb = openpyxl.load_workbook(io.BytesIO(b), data_only=True, read_only=True)
        out, titles = {}, {}
        for ws in wb.worksheets:
            cells = {}
            for r, row in enumerate(ws.iter_rows(values_only=True), start=1):
                for c, v in enumerate(row, start=1):
                    if v not in (None, ""):
                        cells[(r, c)] = v
            out[ws.title] = cells
            heads = [v for (r, c), v in sorted(cells.items()) if r <= 3 and isinstance(v, str) and LATIN.search(v)]
            titles[ws.title] = heads[0].strip() if heads else None
        return out, titles

    def row_label(cells, r):
        txt = [v for (rr, c), v in sorted(cells.items()) if rr == r and isinstance(v, str)]
        en = [t for t in txt if LATIN.search(t)]
        return re.sub(r"\s+", " ", (en or txt or [f"صف {r}"])[0]).strip(" -")

    def same(a, b):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return abs(a - b) <= 1e-9 * max(1, abs(a), abs(b))
        return a == b

    def show(v):
        if v is None:
            return None
        if isinstance(v, float):
            return f"{v:,.4f}".rstrip("0").rstrip(".")
        if isinstance(v, int):
            return f"{v:,}"
        return str(v)

    so, to = load(old)
    sn, tn = load(new)
    tables, changed, added, removed = [], 0, 0, 0
    for sheet in sorted(set(so) | set(sn), key=lambda s: (len(s), s)):
        a, b = so.get(sheet, {}), sn.get(sheet, {})
        ch = []
        for rc in sorted(set(a) | set(b)):
            va, vb = a.get(rc), b.get(rc)
            if same(va, vb):
                continue
            changed += 1
            cells = b if rc in b else a
            ch.append({"row": row_label(cells, rc[0]), "col": rc[1], "cell": f"{get_column_letter(rc[1])}{rc[0]}",
                       "old": show(va), "new": show(vb)})
        if sheet not in so:
            added += 1
        if sheet not in sn:
            removed += 1
        if ch:
            tables.append({"table": sheet, "title": tn.get(sheet) or to.get(sheet), "page": None, "changes": ch})
    return _finish({"kind": "xlsx", "sheets_old": len(so), "sheets_new": len(sn), "changed_cells": changed,
                    "added_rows": added, "removed_rows": removed, "tables": tables, "text_pages": []})


# ---------------------------------------------------------------- shared
def _finish(d: dict) -> dict:
    for t in d["tables"]:
        t["n"] = len(t["changes"])
    n_tab = len(d["tables"])
    total = sum(t["n"] for t in d["tables"])
    if total > MAX_REPORTED:  # keep the stored file small; counts stay complete in "n"
        keep = MAX_REPORTED
        for t in d["tables"]:
            t["changes"], keep = t["changes"][:max(keep, 0)], keep - len(t["changes"])
        d["truncated"] = True
    if not total and not d["text_pages"]:
        d["summary_ar"] = "لم يتغير أي رقم أو نص مقروء؛ الفرق في تنسيق الملف أو بياناته الوصفية فقط."
    else:
        parts = []
        where = "الأوراق" if d["kind"] == "xlsx" else "الجداول"
        if d["changed_cells"]:
            parts.append(f"أرقام تغيّرت: {d['changed_cells']} (في {n_tab} من {where})")
        if d["added_rows"]:
            parts.append(f"{'أوراق' if d['kind'] == 'xlsx' else 'صفوف'} أُضيفت: {d['added_rows']}")
        if d["removed_rows"]:
            parts.append(f"{'أوراق' if d['kind'] == 'xlsx' else 'صفوف'} حُذفت: {d['removed_rows']}")
        if d["text_pages"]:
            parts.append("تغيّر نص في الصفحات " + "، ".join(map(str, d["text_pages"][:10])))
        d["summary_ar"] = "؛ ".join(parts) + "."
    return d


def compare(old: bytes, new: bytes, kind: str) -> dict | None:
    try:
        return compare_xlsx(old, new) if kind.startswith("xls") else compare_pdf(old, new)
    except Exception as e:  # never let a parsing problem stop the monitoring run
        return {"kind": kind, "error": f"{type(e).__name__}: {str(e)[:200]}",
                "summary_ar": "تعذّرت مقارنة المحتوى آلياً؛ قارن النسختين يدوياً.", "tables": [], "text_pages": []}


def lines_ar(d: dict, limit: int = 12) -> list[str]:
    """Markdown lines for an Issue body."""
    out = [f"**ماذا تغيّر:** {d.get('summary_ar', '')}"]
    n = 0
    for t in d.get("tables", []):
        if n >= limit:
            out.append(f"- … وتغييرات أخرى في {'أوراق' if d.get('kind') == 'xlsx' else 'جداول'} إضافية ({len(d['tables']) - d['tables'].index(t)})؛ التفاصيل كاملة في اللوحة.")
            break
        if t.get("table"):
            head = (f"ورقة {t['table']}" if d.get("kind") == "xlsx" else f"جدول {t['table']}") + (f" — {t['title']}" if t.get("title") else "")
            head += f" (صفحة {t['page']})" if t.get("page") else ""
        else:
            head = f"صفحة {t['page']}"
        out.append(f"- **{head}:** تغييرات: {t.get('n', len(t['changes']))}")
        for c in t["changes"][:4]:
            if n >= limit:
                break
            where = c.get("cell") or (f"العمود {c['col']}" if c.get("col") else "")
            if c.get("added"):
                out.append(f"  - صف جديد: {c['row']}: {c['new']}")
            elif c.get("removed"):
                out.append(f"  - صف حُذف: {c['row']}: {c['old']}")
            else:
                out.append(f"  - {c['row']} · {where}: {c['old'] or '—'} ← {c['new'] or '—'}")
            n += 1
    return out
