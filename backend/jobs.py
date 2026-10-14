"""Background jobs: LinkedIn ingest queue, daily internet sweep, notifications and the optional email digest."""
import json
import os
import threading
import time
from datetime import date, datetime, timezone

import agents
import db
import verifier

_ingest_wake = threading.Event()
_ingest_lock = threading.Lock()
_sweep_lock = threading.Lock()
STATE = {"ingest_running": False, "sweep_running": False, "last_sweep": "", "last_sweep_result": "",
         "last_digest": "", "error": ""}


# ------------------------------------------------------------------ settings
def get_setting(key, default=""):
    with db.conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_setting(key, value):
    with db.conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))


def settings():
    return {"notify_email": get_setting("notify_email", "1") == "1",
            "sweep_hour_utc": int(get_setting("sweep_hour_utc", os.getenv("DAILY_SWEEP_HOUR_UTC", "5"))),
            "daily_sweep": get_setting("daily_sweep", "1") == "1"}


# ------------------------------------------------------------------ LinkedIn ingest queue
def kick_ingest():
    _ingest_wake.set()


def queue_counts():
    with db.conn() as c:
        return {r[0]: r[1] for r in c.execute("SELECT status, count(*) FROM ingest_queue GROUP BY status")}


def _process(row):
    import main
    try:
        main.process_post(main.Post(**json.loads(row["post"])))
        status = "done"
    except Exception as e:
        status = "error"
        STATE["error"] = str(e)[:200]
    with db.conn() as c:
        c.execute("UPDATE ingest_queue SET status=? WHERE id=?", (status, row["id"]))


def drain_ingest():
    if not _ingest_lock.acquire(blocking=False):
        return
    try:
        STATE["ingest_running"] = True
        while True:
            with db.conn() as c:
                rows = [dict(r) for r in c.execute("SELECT * FROM ingest_queue WHERE status='pending' ORDER BY id LIMIT 12")]
                for r in rows:
                    c.execute("UPDATE ingest_queue SET status='processing' WHERE id=?", (r["id"],))
            if not rows:
                break
            agents.run_parallel([(str(r["id"]), lambda r=r: _process(r)) for r in rows], workers=3)
            verifier.kick()
    finally:
        STATE["ingest_running"] = False
        _ingest_lock.release()


def _ingest_loop():
    with db.conn() as c:  # anything interrupted by a restart goes back in line
        c.execute("UPDATE ingest_queue SET status='pending' WHERE status='processing'")
    kick_ingest()
    while True:
        _ingest_wake.wait(timeout=60)
        _ingest_wake.clear()
        try:
            drain_ingest()
        except Exception as e:
            STATE["error"] = str(e)[:200]


# ------------------------------------------------------------------ notifications + digest
def unread_count():
    with db.conn() as c:
        return c.execute("SELECT count(*) FROM notifications WHERE read=0").fetchone()[0]


def send_digest():
    """One email to the connected Gmail account itself, listing scholarships found since the last digest."""
    if not settings()["notify_email"]:
        return "digest off"
    import inbox
    svc = inbox.gmail_service()
    me = svc.users().getProfile(userId="me").execute().get("emailAddress")
    last_id = int(get_setting("last_digest_notification_id", "0"))
    with db.conn() as c:
        items = [dict(r) for r in c.execute(
            "SELECT n.id, n.title, n.body, s.url, s.deadline, s.application_mode, s.application_fee "
            "FROM notifications n LEFT JOIN scholarships s ON s.id=n.ref_id "
            "WHERE n.kind='new_scholarship' AND n.id>? ORDER BY n.id", (last_id,))]
    if not items or not me:
        return "nothing new"
    n = len(items)
    link = os.getenv("PUBLIC_URL", "")
    top = sorted(items, key=lambda it: (not it["deadline"], it["deadline"] or ""))[:15]
    lines = [f"{n} new scholarship{'s' if n != 1 else ''} added to ScholarPilot. All of them are open or opening soon "
             f"and match your profile (country, degree and field).", ""]
    for it in top:
        extra = ", ".join(x for x in (it["application_mode"], ("fee " + it["application_fee"]) if it["application_fee"] else "") if x)
        lines += [f"- {it['title'].removeprefix('New: ')} | deadline {it['deadline'] or 'to confirm'}{' | ' + extra if extra else ''}"]
    if n > len(top):
        lines.append(f"- ...and {n - len(top)} more")
    lines += ["", f"Review them and track the ones you want: {link or 'open your ScholarPilot dashboard'}"]
    subject = f"ScholarPilot: {n} new scholarship{'s' if n != 1 else ''} added"
    import base64
    from email.mime.text import MIMEText
    msg = MIMEText("\n".join(lines), "plain", "utf-8")
    msg["To"], msg["Subject"] = me, subject
    svc.users().messages().send(userId="me", body={"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}).execute()
    set_setting("last_digest_notification_id", items[-1]["id"])
    STATE["last_digest"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"sent {len(items)}"


# ------------------------------------------------------------------ daily sweep
def run_daily():
    import main
    import pipeline
    if not _sweep_lock.acquire(blocking=False):
        return "already running"
    try:
        STATE["sweep_running"] = True
        if main.core_missing():
            STATE["last_sweep_result"] = "skipped: profile incomplete"
            return STATE["last_sweep_result"]
        res = pipeline.run_sweep("")
        verifier.kick()
        for _ in range(120):  # wait (up to ~20 min) for verification so the digest only lists verified ones
            time.sleep(10)
            if not verifier.STATE["running"] and not verifier.pending_ids(1):
                break
        digest = ""
        try:
            digest = send_digest()
        except Exception as e:  # Gmail not connected etc. must not fail the sweep
            digest = f"digest skipped: {str(e)[:80]}"
        STATE["last_sweep_result"] = f"{res['found']} results from {res['agents']} agents; {digest}"
        STATE["last_sweep"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return STATE["last_sweep_result"]
    except Exception as e:
        STATE["last_sweep_result"] = f"error: {str(e)[:150]}"
        return STATE["last_sweep_result"]
    finally:
        STATE["sweep_running"] = False
        _sweep_lock.release()


def _digest_when_settled():
    """After a LinkedIn import or a sweep has fully finished (queue empty, verification done), email one summary."""
    busy = STATE["ingest_running"] or STATE["sweep_running"] or verifier.STATE["running"] or verifier.pending_ids(1) \
        or any(k in queue_counts() for k in ("pending", "processing"))
    if busy or not settings()["notify_email"]:
        return
    last_id = int(get_setting("last_digest_notification_id", "0"))
    with db.conn() as c:
        newest = c.execute("SELECT max(id) FROM notifications WHERE kind='new_scholarship'").fetchone()[0] or 0
    if newest > last_id:
        try:
            send_digest()
        except Exception as e:  # Gmail not connected yet etc.
            STATE["error"] = f"digest: {str(e)[:120]}"


def _schedule_loop():
    time.sleep(90)
    while True:
        try:
            st = settings()
            now = datetime.now(timezone.utc)
            today = now.date().isoformat()
            if st["daily_sweep"] and now.hour >= st["sweep_hour_utc"] and get_setting("last_sweep_date") != today:
                set_setting("last_sweep_date", today)  # set first: a crash must not retry in a tight loop
                run_daily()
            _digest_when_settled()
        except Exception as e:
            STATE["error"] = str(e)[:200]
        time.sleep(300)


def start():
    threading.Thread(target=_ingest_loop, daemon=True, name="ingest").start()
    threading.Thread(target=_schedule_loop, daemon=True, name="scheduler").start()
