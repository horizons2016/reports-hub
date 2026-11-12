"""Merge a harvest into the stored catalogue and detect what changed.

Rule (see the methodology page):
  * a publication is identified by NCSI's publication id (or, failing that, the file's title slug);
  * each file slot (pdf_en, pdf_ar, xls_en, xls_ar) keeps a list of versions;
  * a new link in a slot = a new upload. Its content fingerprint (SHA-256) decides:
      - same fingerprint as the latest version  -> "reupload"   (logged, no alert)
      - different fingerprint                   -> "revision"   (alert)
      - older upload time than the latest       -> "late_older" (logged)
  * a slot that appears for the first time on a known publication -> "file_added" (e.g. Excel arrives)
  * during the provisional window the current file is re-fingerprinted each run; a changed
    fingerprint at the same link -> "revision_same_url" (alert)
  * historical signal, before monitoring began: current file uploaded more than ~15 minutes
    after the record was created -> replaced at least once after release.
"""
from __future__ import annotations

import re
from datetime import timedelta

from common import (ARCHIVE_OUT, days_between, fetch_file, file_ts, iso, log, now_utc,
                    parse_time, settings)
from harvest import frequency, series_name

SLOTS = ("pdf_en", "pdf_ar", "xls_en", "xls_ar")


def _uploaded(ts: int | None) -> str | None:
    from datetime import datetime, timezone
    return iso(datetime.fromtimestamp(ts, timezone.utc)) if ts else None


def _archive_name(url: str, version: int, same_url: bool) -> str:
    name = url.rsplit("/", 1)[-1]
    if same_url:
        stem, ext = name.rsplit(".", 1)
        name = f"{stem}__v{version}.{ext}"
    return name


def _fingerprint(url: str, tag: str | None, name: str | None) -> dict | None:
    keep = ARCHIVE_OUT / tag / name if (tag and name and settings().get("archive_files")) else None
    return fetch_file(url, keep_to=keep)


def _drop_archive(tag: str, name: str) -> None:
    p = ARCHIVE_OUT / tag / name
    if p.exists():
        p.unlink()


def _release_tag(at) -> str:
    return "archive-" + at.strftime("%Y-%m")


def _repo_download(tag: str, name: str) -> str:
    import os
    repo = os.environ.get("GITHUB_REPOSITORY", "horizons2016/reports-hub")
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def derive(pub: dict) -> None:
    """Fields computed from the record: frequency, series, release time, historical replacement."""
    pub["frequency"] = frequency(pub.get("title_en"), pub.get("title_ar"))
    pub["series"] = series_name(pub.get("title_en") or pub.get("title_ar"))
    release = parse_time(pub.get("created_at")) or parse_time(pub.get("published_date"))
    pub["released_at"] = iso(release)
    main = pub["files"].get("pdf_en") or pub["files"].get("pdf_ar") or next(iter(pub["files"].values()), None)
    delay = None
    if main and main.get("ts") and release:
        from datetime import datetime, timezone
        delay = days_between(release, datetime.fromtimestamp(main["ts"], timezone.utc))
    pub["current_file_delay_days"] = delay
    # files named publicationpdf<ts>/publicationxls<ts> were re-uploaded in bulk when NCSI moved its
    # archive to the new site (Sep-Oct 2025): a migration, not a revision
    pub["migrated"] = any(re.search(r"/publication(pdf|xls)\d{10}\.", f.get("url", "")) for f in pub["files"].values())
    pub["replaced_after_release"] = bool(delay is not None and delay > 0.01 and not pub["migrated"])
    prov = settings().get("provisional_days", 15)
    pub["provisional"] = bool(release and now_utc() - release <= timedelta(days=prov))
    pub["version_count"] = max((len(pub["versions"].get(s, [])) for s in pub["versions"]), default=0)


def merge(state: dict, harvested: list[dict], baseline: bool) -> list[dict]:
    """Update state["publications"] in place; return events."""
    pubs: dict = state.setdefault("publications", {})
    events: list[dict] = []
    backfilled: list[str] = []
    now = now_utc()
    tag = _release_tag(now)
    recent_days = settings().get("hash_recent_days_on_baseline", 45)

    for rec in harvested:
        pid = rec["id"]
        cur = pubs.get(pid)
        is_new = cur is None
        if is_new:
            cur = {k: v for k, v in rec.items() if k != "files"}
            cur.update({"files": {}, "versions": {}, "first_seen": iso(now)})
            pubs[pid] = cur
        else:
            for k, v in rec.items():
                if k != "files" and v not in (None, ""):
                    cur[k] = v
        cur["last_seen"] = iso(now)

        release = parse_time(rec.get("created_at")) or parse_time(rec.get("published_date"))
        recent = bool(release and now - release <= timedelta(days=recent_days))
        # an old publication seen for the first time (e.g. the API listed it late) is history, not news
        quiet = baseline or (is_new and release is not None and now - release > timedelta(days=30))
        if quiet and is_new and not baseline:
            backfilled.append(pid)

        for slot, f in rec["files"].items():
            url, ts = f["url"], f.get("ts")
            versions = cur["versions"].setdefault(slot, [])
            if versions and versions[-1]["url"] == url:
                continue  # unchanged link
            if any(v["url"] == url for v in versions):
                continue  # an older link reappeared; ignore
            prev = versions[-1] if versions else None
            vno = len(versions) + 1
            name = _archive_name(url, vno, False)

            if quiet and is_new:
                fp = _fingerprint(url, tag, name) if recent else None
                status = "baseline"
            else:
                fp = _fingerprint(url, tag, name)
                if prev is None:
                    status = "new" if is_new else "file_added"
                else:
                    if not prev.get("sha256"):  # fingerprint the previous version if it is still online
                        old = fetch_file(prev["url"])
                        if old:
                            prev.update(old)
                    if ts and prev.get("ts") and ts < prev["ts"]:
                        status = "late_older"
                    elif fp and prev.get("sha256") and fp["sha256"] == prev["sha256"]:
                        status = "reupload"
                    elif fp and prev.get("sha256"):
                        status = "revision"
                    else:
                        status = "new_upload_unverified"

            v = {"v": vno, "url": url, "ts": ts, "uploaded": _uploaded(ts), "seen_at": iso(now), "status": status,
                 "sha256": fp["sha256"] if fp else None, "size": fp["size"] if fp else None}
            if status == "reupload":  # identical content: keep one archived copy only
                _drop_archive(tag, name)
                v["archive_url"] = prev.get("archive_url")
            elif fp and settings().get("archive_files"):
                v["archive_url"] = _repo_download(tag, name)
            versions.append(v)
            cur["files"][slot] = {"url": url, "ts": ts}

            if status != "baseline":
                events.append({
                    "at": iso(now), "type": status, "id": pid, "slot": slot, "version": vno,
                    "title_ar": cur.get("title_ar"), "title_en": cur.get("title_en"),
                    "url": url, "uploaded": v["uploaded"],
                    "previous": {"v": prev["v"], "url": prev["url"], "uploaded": prev.get("uploaded"),
                                 "archive_url": prev.get("archive_url")} if prev else None,
                })
        derive(cur)

    if backfilled:
        log(f"merge: {len(backfilled)} older publications recorded as history without alerts")
    # publications that were new this run: collapse their slot events into one "new" event
    collapsed, seen_new = [], set()
    for e in events:
        if e["type"] == "new":
            if e["id"] in seen_new:
                continue
            seen_new.add(e["id"])
            e["slot"] = "all"
        collapsed.append(e)
    return collapsed


def recheck_provisional(state: dict) -> list[dict]:
    """Re-fingerprint current files of publications still in the provisional window."""
    events = []
    now = now_utc()
    tag = _release_tag(now)
    for pid, pub in state.get("publications", {}).items():
        if not pub.get("provisional"):
            continue
        for slot, versions in pub.get("versions", {}).items():
            last = versions[-1] if versions else None
            if not last or not last.get("sha256"):
                continue
            vno = len(versions) + 1
            name = _archive_name(last["url"], vno, True)
            fp = _fingerprint(last["url"], tag, name)
            if fp and fp["sha256"] == last["sha256"]:
                _drop_archive(tag, name)
            if fp and fp["sha256"] != last["sha256"]:
                v = {**last, "v": vno, "seen_at": iso(now), "status": "revision_same_url",
                     "sha256": fp["sha256"], "size": fp["size"]}
                if settings().get("archive_files"):
                    v["archive_url"] = _repo_download(tag, name)
                versions.append(v)
                events.append({"at": iso(now), "type": "revision_same_url", "id": pid, "slot": slot, "version": vno,
                               "title_ar": pub.get("title_ar"), "title_en": pub.get("title_en"), "url": last["url"],
                               "uploaded": last.get("uploaded"),
                               "previous": {"v": last["v"], "url": last["url"], "uploaded": last.get("uploaded"),
                                            "archive_url": last.get("archive_url")}})
                derive(pub)
    if events:
        log(f"recheck: {len(events)} same-link replacements")
    return events
