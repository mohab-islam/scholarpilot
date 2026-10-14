"""Background verification: every automatically discovered scholarship is checked (dates, documents,
eligibility) and only open/upcoming ones you can apply for are shown by default.

verdicts: pending (not checked yet) · qualified (shown) · review (dates not found) · rejected (hidden, with a reason)
"""
import threading
from datetime import date

import agents
import ai
import db

_wake = threading.Event()
_run_lock = threading.Lock()
STATE = {"running": False, "last_run": "", "checked": 0, "last_error": ""}
WORKERS = 4


def today():
    return date.today().isoformat()


def kick():
    """Wake the verifier (called whenever something new is stored)."""
    _wake.set()


def _parse(d):
    try:
        return date.fromisoformat(d) if d else None
    except ValueError:
        return None


LINKEDIN_SOURCES = ("linkedin", "linkedin-link")


def reconcile(research_deadline, page_deadline):
    """Compare the web-research date with the date read from the official page.
    Returns (deadline_to_use, confidence, note). The official page wins when they disagree."""
    r, p = _parse(research_deadline), _parse(page_deadline)
    if r and p:
        if r == p:
            return p.isoformat(), "confirmed", ""
        return p.isoformat(), "official", f"Date corrected from the official page ({p.isoformat()}); web research said {r.isoformat()}"
    if p:
        return p.isoformat(), "official", ""
    if r:
        return r.isoformat(), "single-source", "Date not found on the official page; confirm before applying"
    return "", "", ""


def decide(row, wanted_levels):
    """Pure decision from a (researched) scholarship row. Returns (verdict, reason)."""
    deadline, opens = _parse(row.get("deadline")), _parse(row.get("open_date"))
    now = date.today()
    if row.get("source") in LINKEDIN_SOURCES and opens and opens > now and not (deadline and deadline < now):
        return "rejected", f"Not open yet (opens {opens.isoformat()})"  # LinkedIn finds must be open now
    if (row.get("fit") or "").lower() == "no":
        return "rejected", "Not eligible for you: " + (row.get("fit_reason") or "eligibility rules exclude your profile")
    if wanted_levels and row.get("level"):
        from main import level_set
        have = level_set(row.get("level"))
        if have and not (wanted_levels & have):
            return "rejected", f"Different degree level ({row.get('level')})"
    if deadline and deadline < now:
        return "rejected", f"Closed on {deadline.isoformat()}"
    if deadline:
        return "qualified", ""
    if opens and opens >= now:
        return "qualified", ""
    return "review", "Opening and closing dates could not be verified"


def verify_one(sid):
    import main
    with db.conn() as c:
        row = db.row(c.execute("SELECT * FROM scholarships WHERE id=?", (sid,)).fetchone())
    if not row:
        return
    try:
        res = ai.research_scholarship(row["name"], row["url"], today())
        if isinstance(res, list):
            res = res[0] if res else {}
        res["name"], res["url"] = row["name"], row["url"] or res.get("url")
        main.save_scholarship(res, "research")  # fills dates, documents, eligibility, fit on the existing row
        with db.conn() as c:
            row = db.row(c.execute("SELECT * FROM scholarships WHERE id=?", (sid,)).fetchone())
        # second, independent check: read the official page itself and compare
        page_deadline = ""
        if row.get("url"):
            try:
                text = main.fetch_page(row["url"])[0]
                found = ai.extract_scholarships(text[:20000], today())
                best = max(found, key=lambda f: len(main.name_tokens(f.get("name")) & main.name_tokens(row["name"])), default={})
                page_deadline = best.get("deadline") or ""
            except Exception:
                page_deadline = ""
        use, confidence, note = reconcile(row.get("deadline"), page_deadline)
        with db.conn() as c:
            if use and use != row.get("deadline"):
                c.execute("UPDATE scholarships SET deadline=? WHERE id=?", (use, sid))
                row["deadline"] = use
            c.execute("UPDATE scholarships SET confidence=? WHERE id=?", (confidence, sid))
        row["confidence"] = confidence
        verdict, reason = decide(row, main.level_set(main.get_profile().get("target_level")))
        if verdict == "qualified" and note:
            reason = note  # shown as a hint on the card
        if row.get("pinned"):
            verdict, reason = "qualified", ""  # never hide something you decided to track
    except Exception as e:
        STATE["last_error"] = str(e)[:200]
        verdict, reason = "review", "Could not be verified automatically"
    with db.conn() as c:
        c.execute("UPDATE scholarships SET verdict=?, verdict_reason=?, verified_at=? WHERE id=?",
                  (verdict, reason, today(), sid))
        if (verdict == "qualified" and row and row.get("source") not in ("manual", "research") and not row.get("pinned")
                and not c.execute("SELECT 1 FROM notifications WHERE kind='new_scholarship' AND ref_id=?", (sid,)).fetchone()):
            when = row.get("deadline") or (f"opens {row['open_date']}" if row.get("open_date") else "dates to confirm")
            c.execute("INSERT INTO notifications(kind,title,body,ref_id) VALUES('new_scholarship',?,?,?)",
                      (f"New: {row['name']}", f"{row.get('provider') or ''} · deadline {when}".strip(" ·"), sid))


def pending_ids(limit=40):
    with db.conn() as c:
        return [r[0] for r in c.execute("SELECT id FROM scholarships WHERE verdict='pending' ORDER BY id LIMIT ?", (limit,))]


def run_until_idle():
    if not _run_lock.acquire(blocking=False):
        return 0  # a run is already going and will pick everything up
    done = 0
    try:
        STATE["running"] = True
        while True:
            ids = pending_ids()
            if not ids:
                break
            agents.run_parallel([(str(i), lambda i=i: verify_one(i)) for i in ids], workers=WORKERS)
            done += len(ids)
        try:
            import main
            main.merge_duplicates()  # no overlaps between sources
        except Exception as e:
            STATE["last_error"] = str(e)[:200]
    finally:
        STATE.update(running=False, last_run=today(), checked=STATE["checked"] + done)
        _run_lock.release()
    return done


def _loop():
    while True:
        _wake.wait(timeout=120)
        _wake.clear()
        try:
            run_until_idle()
        except Exception as e:  # never let the worker die
            STATE["last_error"] = str(e)[:200]


def start():
    threading.Thread(target=_loop, daemon=True, name="verifier").start()
    kick()  # check anything left pending from before a restart
