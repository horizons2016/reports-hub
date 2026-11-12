"""Find NCSI's publication list and normalise it into records.

NCSI's website is a JavaScript app that loads its publication list from an API on
api.ncsi.gov.om. The exact route is not documented, so this module:

1. Re-uses API endpoints found on earlier runs (data/discovery.json), calling them directly.
2. Otherwise opens the publications page in a headless browser, records every JSON response
   from *.ncsi.gov.om, and keeps the ones that contain publication files.
3. Follows pagination on those endpoints (Laravel-style `next_page_url` / `last_page`, or a
   `page` query parameter) to read the whole catalogue.
4. As a last resort, reads file links straight from the rendered page.

Whatever shape the API returns, records are recognised by containing a link to
/uploads/pdfs/ or /uploads/xls/, and normalised into one schema.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from common import (DATA, FILE_RE, file_ts, http_get, load_json, log, now_utc, iso,
                    parse_time, save_json, settings)

DISCOVERY = DATA / "discovery.json"
MONTHS = ("january february march april may june july august september october november december "
          "jan feb mar apr jun jul aug sep sept oct nov dec").split()
AR_MONTHS = "يناير فبراير مارس أبريل ابريل مايو يونيو يوليو أغسطس اغسطس سبتمبر أكتوبر اكتوبر نوفمبر ديسمبر".split()


# ---------------------------------------------------------------- normalisation
def _abs(u: str) -> str:
    u = u.strip()
    if u.startswith("http"):
        return u.replace("http://", "https://", 1)
    return settings().get("file_host", "https://api.ncsi.gov.om/") + u.lstrip("/")


def _slot(key: str, url: str) -> str:
    """Which file slot a link fills: pdf_en, pdf_ar, xls_en, xls_ar."""
    kind = "xls" if "/xls/" in url or url.lower().endswith((".xls", ".xlsx")) else "pdf"
    k = (key or "").lower()
    name = url.rsplit("/", 1)[-1].lower()
    if k.endswith(("_ar", "ar")) or "arabic" in k or re.search(r"_ar_\d{10}\.", name):
        lang = "ar"
    else:
        lang = "en"
    return f"{kind}_{lang}"


def _walk_strings(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, prefix)
    elif isinstance(obj, str):
        yield prefix, obj


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _text(v):
    if isinstance(v, dict):  # {"ar": "...", "en": "..."} style
        return v
    return str(v).strip() if v not in (None, "") else None


def normalise(raw: dict) -> dict | None:
    """Turn one API object that contains file links into a publication record."""
    files = {}
    for key, s in _walk_strings(raw):
        for m in FILE_RE.findall(s):
            url = _abs(m)
            slot = _slot(key.split(".")[-1], url)
            if slot not in files:  # first link per slot wins
                files[slot] = {"url": url, "ts": file_ts(url)}
    if not files:
        return None

    title_en = _first(raw, "title_en", "name_en", "titleEn", "title")
    title_ar = _first(raw, "title_ar", "name_ar", "titleAr")
    if isinstance(title_en, dict):
        title_ar = title_ar or title_en.get("ar")
        title_en = title_en.get("en")
    cat = raw.get("category") if isinstance(raw.get("category"), dict) else {}
    category_en = _first(raw, "category_en", "categoryEn") or _first(cat, "name_en", "title_en", "name")
    category_ar = _first(raw, "category_ar", "categoryAr") or _first(cat, "name_ar", "title_ar")

    pid = _first(raw, "publication_id", "id", "publicationId")
    any_url = next(iter(files.values()))["url"]
    slug = re.sub(r"(_ar)?_\d{10}\.(pdf|xlsx?)$", "", any_url.rsplit("/", 1)[-1], flags=re.I)
    rid = f"ncsi-pub-{pid}" if pid not in (None, "") and str(pid).isdigit() else f"ncsi-file-{slug}"

    if not title_en and not title_ar:
        title_en = re.sub(r"_+", " ", slug).strip().title()

    rec = {
        "id": rid,
        "publication_id": int(pid) if pid not in (None, "") and str(pid).isdigit() else None,
        "title_en": _text(title_en),
        "title_ar": _text(title_ar),
        "category_en": _text(category_en),
        "category_ar": _text(category_ar),
        "classification_id": _first(raw, "classification_id", "classificationId"),
        "published_date": _text(_first(raw, "published_date", "publish_date", "publishedDate", "date")),
        "created_at": _text(_first(raw, "created_at", "createdAt")),
        "updated_at": _text(_first(raw, "updated_at", "updatedAt")),
        "cover_url": None,
        "files": files,
    }
    cover = _first(raw, "cover_image_url", "cover_image", "coverimage", "image")
    if isinstance(cover, str) and "uploads" in cover:
        rec["cover_url"] = _abs(cover)
    return rec


def extract_records(payload) -> list[dict]:
    """Find every object in a JSON payload that represents a publication."""
    found: list[dict] = []

    def visit(o):
        if isinstance(o, dict):
            direct = any(isinstance(v, str) and FILE_RE.search(v) for v in o.values())
            nested_obj = any(isinstance(v, dict) and any(isinstance(x, str) and FILE_RE.search(x) for x in v.values())
                             for v in o.values())
            has_title = any(k in o for k in ("title", "title_en", "title_ar", "name_en", "publication_id"))
            if direct or (nested_obj and has_title):
                rec = normalise(o)
                if rec:
                    found.append(rec)
                    return
            for v in o.values():
                visit(v)
        elif isinstance(o, list):
            for v in o:
                visit(v)

    visit(payload)
    return found


# ---------------------------------------------------------------- pagination
def _with_param(url: str, **params) -> str:
    p = urlparse(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q.update({k: str(v) for k, v in params.items()})
    return urlunparse(p._replace(query=urlencode(q)))


def _get_json(url: str):
    r = http_get(url, timeout=60, headers={"Accept": "application/json"})
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def crawl_endpoint(url: str, first=None, max_pages: int = 400) -> list[dict]:
    """Read every page of a list endpoint."""
    data = first if first is not None else _get_json(url)
    if data is None:
        return []
    # ask for bigger pages when the API allows it
    for size_param in ("per_page", "limit", "pageSize", "size"):
        if size_param in url:
            big = _get_json(_with_param(url, **{size_param: 200}))
            if big is not None and len(extract_records(big)) > len(extract_records(data)):
                url, data = _with_param(url, **{size_param: 200}), big
            break

    records = extract_records(data)
    seen_pages = {url}
    page_no = 1
    for _ in range(max_pages):
        nxt = None
        if isinstance(data, dict):
            links = data.get("links") if isinstance(data.get("links"), dict) else {}
            nxt = data.get("next_page_url") or links.get("next")
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else data
            last = meta.get("last_page") or meta.get("lastPage") or meta.get("total_pages") or meta.get("totalPages")
            try:
                if not nxt and last and page_no < int(last):
                    nxt = _with_param(url, page=page_no + 1)
            except (TypeError, ValueError):
                pass
        if not nxt and records and "page=" in url:
            nxt = _with_param(url, page=page_no + 1)
        if not nxt or nxt in seen_pages:
            break
        seen_pages.add(nxt)
        page_no += 1
        data = _get_json(nxt)
        if data is None:
            break
        more = extract_records(data)
        if not more:
            break
        records += more
    return records


# ---------------------------------------------------------------- discovery
PAGE_KEYS = ("page", "pagenumber", "pageno", "page_number", "current_page", "currentpage", "pageindex", "page_index")
OFFSET_KEYS = ("offset", "skip", "start")
SIZE_KEYS = ("limit", "per_page", "perpage", "pagesize", "page_size", "size", "take", "length", "rows", "count")


def _find_key(d: dict, names) -> str | None:
    for k in d:
        if str(k).lower() in names:
            return k
    return None


def _replay(ctx, req: dict, max_pages: int = 300) -> list[dict]:
    """Page through a list API by re-sending the website's own request with changed paging fields."""
    body = None
    if req.get("post_data"):
        try:
            body = json.loads(req["post_data"])
        except ValueError:
            body = None
    p = urlparse(req["url"])
    query = dict(parse_qsl(p.query, keep_blank_values=True))
    target = body if isinstance(body, dict) else query
    page_k, off_k, size_k = _find_key(target, PAGE_KEYS), _find_key(target, OFFSET_KEYS), _find_key(target, SIZE_KEYS)
    log(f"replay: {req['method']} {req['url']} body={'json' if isinstance(body, dict) else 'none'} "
        f"page_key={page_k} offset_key={off_k} size_key={size_k}")
    headers = {k: v for k, v in req.get("headers", {}).items()
               if k.lower() not in ("content-length", "host", "connection", "accept-encoding")}

    def send(t: dict):
        url, data = req["url"], None
        if isinstance(body, dict):
            data = json.dumps(t)
        else:
            url = urlunparse(p._replace(query=urlencode(t)))
        try:
            r = ctx.request.fetch(url, method=req["method"], headers=headers, data=data, timeout=60000)
            return r.json() if r.ok else None
        except Exception as e:
            log("replay: request failed", type(e).__name__, str(e)[:120])
            return None

    t = dict(target)
    size = 6
    if size_k:
        orig = target[size_k]
        try:
            size = int(orig)
        except (TypeError, ValueError):
            pass
        t[size_k] = 100 if isinstance(orig, int) else "100"
        first = send(t)
        got = len(extract_records(first)) if first is not None else 0
        if got > size:
            size = got
        else:
            t[size_k] = orig
    if not page_k and not off_k:
        return extract_records(send(t) or {})

    out, seen, empty = [], set(), 0
    start = int(t.get(page_k, 1) or 1) if page_k else int(t.get(off_k, 0) or 0)
    for i in range(max_pages):
        if page_k:
            t[page_k] = start + i if isinstance(target[page_k], int) else str(start + i)
        else:
            t[off_k] = start + i * size if isinstance(target[off_k], int) else str(start + i * size)
        data = send(t)
        recs = extract_records(data) if data is not None else []
        new = [r for r in recs if r["id"] not in seen]
        if not new:
            empty += 1
            if empty >= 2:
                break
            continue
        empty = 0
        seen.update(r["id"] for r in new)
        out += new
    log(f"replay: {len(out)} records over {i + 1} requests (page size {size})")
    return out


def browser_discover(pages: list[str]) -> tuple[list[dict], list[str], list[dict]]:
    """Open the pages in headless Chromium, find the publication API, and page through it.

    Returns (records, api_urls, info)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("playwright not installed; skipping browser discovery")
        return [], [], []
    payloads: list[tuple[dict, object]] = []
    html_links: set[str] = set()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="ar-OM")
        page = ctx.new_page()

        def on_response(resp):
            try:
                host = urlparse(resp.url).hostname or ""
                ctype = (resp.headers.get("content-type") or "").lower()
                if host.endswith("ncsi.gov.om") and "json" in ctype and resp.status == 200:
                    rq = resp.request
                    payloads.append(({"url": resp.url, "method": rq.method, "post_data": rq.post_data,
                                      "headers": rq.headers}, resp.json()))
            except Exception:
                pass

        page.on("response", on_response)
        for url in pages:
            try:
                log("browser: opening", url)
                page.goto(url, wait_until="networkidle", timeout=90000)
                for _ in range(8):  # scroll and press "more" buttons to trigger pagination calls
                    page.mouse.wheel(0, 5000)
                    page.wait_for_timeout(700)
                    for label in ("Load more", "Show more", "المزيد", "عرض المزيد", "تحميل المزيد", "Next", "التالي"):
                        btn = page.get_by_text(label, exact=False)
                        if btn.count():
                            try:
                                btn.first.click(timeout=1500)
                                page.wait_for_timeout(1200)
                            except Exception:
                                pass
                html_links |= {m for m in FILE_RE.findall(page.content())}
            except Exception as e:
                log("browser: could not open", url, type(e).__name__, str(e)[:160])

        # which captured requests returned publications?
        api_reqs, seen_keys, seen_recs, info = [], set(), [], []
        for rq, body in payloads:
            recs = extract_records(body)
            info.append({"url": rq["url"], "method": rq["method"], "post_data": (rq["post_data"] or "")[:400],
                         "records": len(recs), "top_keys": list(body)[:12] if isinstance(body, dict) else f"list[{len(body)}]"})
            if recs:
                seen_recs += recs
                key = (rq["method"], rq["url"], rq["post_data"])
                if key not in seen_keys:
                    seen_keys.add(key)
                    api_reqs.append(rq)
        log(f"browser: {len(payloads)} JSON responses, {len(api_reqs)} distinct publication requests, "
            f"{len(seen_recs)} records seen, {len(html_links)} file links in HTML")

        # page through each publication request inside the same browser session
        replayed = []
        for rq in api_reqs[:6]:
            replayed += _replay(ctx, rq)
        browser.close()

    html_recs = [r for r in (normalise({"file": _abs(u)}) for u in html_links) if r]
    candidates = {"api-replay": replayed, "browser-json": seen_recs, "html-links": html_recs}
    method = max(candidates, key=lambda k: len({r["id"] for r in candidates[k]}))
    records = candidates[method]
    if method != "api-replay" and replayed:
        records = replayed + records  # keep API metadata where we have it
    log(f"browser: using {method} ({len({r['id'] for r in candidates[method]})} publications); "
        + ", ".join(f"{k}={len({r['id'] for r in v})}" for k, v in candidates.items()))
    info.append({"chosen": method})
    return records, [rq["url"] for rq in api_reqs if rq["method"] == "GET"], info


def _endpoint_key(url: str) -> str:
    p = urlparse(url)
    q = {k: v for k, v in parse_qsl(p.query) if k.lower() not in ("page", "per_page", "limit", "offset")}
    return urlunparse(p._replace(query=urlencode(q)))


def harvest() -> dict:
    """Return {"records": [...], "method": str, "endpoints": [...]}."""
    disc = load_json(DISCOVERY, {"endpoints": []})
    records: list[dict] = []
    method = None

    # 1) known endpoints, called directly
    for ep in disc.get("endpoints", []):
        recs = crawl_endpoint(ep)
        log(f"direct: {ep} -> {len(recs)} records")
        records += recs
    if records:
        method = "api-direct"

    # 2) browser discovery
    if not records:
        recs, api_urls, info = browser_discover(settings().get("publication_pages", []))
        endpoints = sorted({_endpoint_key(u) for u in api_urls})
        crawled = []
        for ep in endpoints:
            got = crawl_endpoint(ep)
            log(f"crawl: {ep} -> {len(got)} records")
            crawled += got
        chosen = next((i["chosen"] for i in info if "chosen" in i), "browser")
        records = crawled if len({r["id"] for r in crawled}) > len({r["id"] for r in recs}) else recs
        method = "api-direct-get" if records is crawled and crawled else chosen
        disc["endpoints"] = endpoints  # only GET endpoints that work without the browser
        disc["last_discovery"] = {"at": iso(now_utc()), "responses": info[:40], "method": method}
        save_json(DISCOVERY, disc)

    # de-duplicate (same id seen on several pages or endpoints): merge file slots
    merged: dict[str, dict] = {}
    for r in records:
        cur = merged.get(r["id"])
        if cur is None:
            merged[r["id"]] = r
        else:
            for slot, f in r["files"].items():
                if slot not in cur["files"] or (f.get("ts") or 0) > (cur["files"][slot].get("ts") or 0):
                    cur["files"][slot] = f
            for k, v in r.items():
                if k != "files" and cur.get(k) in (None, "") and v not in (None, ""):
                    cur[k] = v
    log(f"harvest: {len(merged)} publications via {method}")
    return {"records": list(merged.values()), "method": method or "none", "endpoints": disc.get("endpoints", [])}


# ---------------------------------------------------------------- derived fields
def frequency(title_en: str | None, title_ar: str | None) -> str:
    t = f"{title_en or ''} {title_ar or ''}".lower()
    if "weekly" in t or "أسبوع" in t:
        return "weekly"
    if "monthly" in t or "شهري" in t or any(re.search(rf"\b{m}\b", t) for m in MONTHS) or any(m in t for m in AR_MONTHS):
        return "monthly"
    if re.search(r"\bq[1-4]\b", t) or "quarter" in t or "ربع" in t or re.search(r"الربع (الأول|الثاني|الثالث|الرابع)", t):
        return "quarterly"
    if re.search(r"\b(19|20)\d{2}\b", t):
        return "annual"
    return "other"


def series_name(title: str | None) -> str:
    """'Monthly Statistical Bulletin :September 2026' -> 'Monthly Statistical Bulletin'."""
    if not title:
        return ""
    t = title.split(":")[0] if ":" in title else title
    t = re.sub(r"\b(19|20)\d{2}(\s*[-–/]\s*(19|20)?\d{2})?\b", " ", t)
    t = re.sub(r"\bQ[1-4]\b|\bIssue\s*\d+\b|العدد\s*\d+", " ", t, flags=re.I)
    t = re.sub("|".join(rf"\b{m}\b" for m in MONTHS), " ", t, flags=re.I)
    for m in AR_MONTHS:
        t = t.replace(m, " ")
    return re.sub(r"\s+", " ", t).strip(" -–:،,") or title.strip()
