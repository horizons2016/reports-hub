"""Entry point: python collector/run.py [--no-issues] [--no-browser]

Reads NCSI's catalogue and SDMX files, updates data/*.json, logs events, opens GitHub Issues,
and writes the Atom feed. Safe to run repeatedly: it only reports what changed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA, iso, load_json, log, now_utc, save_json  # noqa: E402
import alerts  # noqa: E402
import harvest as harvest_mod  # noqa: E402
import nsdp  # noqa: E402
import versions  # noqa: E402

STATE = DATA / "publications.json"
NSDP = DATA / "nsdp.json"
EVENTS = DATA / "events.json"
STATUS = DATA / "status.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-issues", action="store_true", help="do not open GitHub Issues")
    ap.add_argument("--no-browser", action="store_true", help="skip headless-browser discovery")
    ap.add_argument("--skip-nsdp", action="store_true")
    args = ap.parse_args()

    started = now_utc()
    state = {"publications": {p["id"]: p for p in load_json(STATE, {"publications": []})["publications"]},
             "nsdp": {e["key"]: e for e in load_json(NSDP, {"categories": []})["categories"]}}
    events_log = load_json(EVENTS, {"events": []})["events"]
    status = load_json(STATUS, {})
    baseline = not state["publications"]

    # 1) publications
    if args.no_browser:
        harvest_mod.browser_discover = lambda pages: ([], [], [])
    h = harvest_mod.harvest()
    pub_events = []
    if h["records"]:
        pub_events = versions.merge(state, h["records"], baseline)
        pub_events += versions.recheck_provisional(state) if not baseline else []
    else:
        log("WARNING: no publications harvested this run")

    # 2) SDMX
    nsdp_baseline = not state["nsdp"]
    nsdp_events = [] if args.skip_nsdp else nsdp.check(state, nsdp_baseline)

    new_events = pub_events + nsdp_events
    extra = []
    if baseline and h["records"]:
        n = len(state["publications"])
        replaced = sum(1 for p in state["publications"].values() if p.get("replaced_after_release"))
        extra.append({"issue_title": f"بدء الرصد: {n} إصداراً من المركز",
                      "issue_body": (f"اكتمل أول تشغيل. سُجّل {n} إصداراً كنسخة أولى (v1).\n\n"
                                     f"منها {replaced} إصداراً استُبدل ملفه بعد النشر مرة واحدة على الأقل قبل بدء الرصد.\n\n"
                                     "من الآن: كل إصدار جديد أو تعديل يصلكم تنبيهاً هنا."),
                      "type": "baseline"})
    if not args.no_issues:
        alerts.open_issues(new_events, extra)
    events_log += new_events
    events_log = events_log[-2000:]
    alerts.write_feed(events_log)

    pubs = sorted(state["publications"].values(), key=lambda p: p.get("released_at") or "", reverse=True)
    save_json(STATE, {"updated_at": iso(now_utc()), "publications": pubs})
    save_json(NSDP, {"updated_at": iso(now_utc()), "categories": list(state["nsdp"].values())})
    save_json(EVENTS, {"updated_at": iso(now_utc()), "events": events_log})
    import os
    status.update({
        "repository": os.environ.get("GITHUB_REPOSITORY", status.get("repository", "horizons2016/reports-hub")),
        "last_run": iso(started), "finished": iso(now_utc()),
        "harvest_method": h["method"], "endpoints": h["endpoints"],
        "publications": len(pubs), "events_this_run": len(new_events),
        "baseline_run": baseline, "harvest_ok": bool(h["records"]),
        "nsdp_ok": sum(1 for e in state["nsdp"].values() if e.get("status") == "ok"),
    })
    if baseline and h["records"]:
        status["monitoring_since"] = iso(started)
    save_json(STATUS, status)
    log(f"done: {len(pubs)} publications, {len(new_events)} new events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
