"""Gmail integration, to-do lists pulled from official pages, mail-driven status updates, dashboard data."""
import base64
import json
import os
import threading
import time
from datetime import date, datetime
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

import agents
import ai
import db

router = APIRouter(prefix="/api")

DATA = db.DATA_DIR
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
_pending = {}
CRED = DATA / "gmail_credentials.json"
TOKEN = DATA / "gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose"]
_flow = {"running": False, "error": ""}


def m():
    import main
    return main


def today():
    return date.today().isoformat()


def call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Error: {e}")


# =====================================================================
# Gmail
# =====================================================================
def gmail_creds():
    if not TOKEN.exists():
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN.write_text(creds.to_json())
    return creds if creds.valid else None


def gmail_service():
    from googleapiclient.discovery import build
    creds = gmail_creds()
    if not creds:
        raise HTTPException(400, "Gmail is not connected. Open Inbox & Gmail and connect it.")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


@router.get("/gmail/status")
def gmail_status():
    connected, email = False, ""
    try:
        if gmail_creds():
            connected = True
            email = gmail_service().users().getProfile(userId="me").execute().get("emailAddress", "")
    except Exception as e:
        _flow["error"] = _flow["error"] or str(e)
    return {"has_credentials": CRED.exists(), "connected": connected, "email": email,
            "connecting": _flow["running"], "error": _flow["error"]}


@router.post("/gmail/credentials")
async def upload_credentials(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        data = json.loads(raw)
        assert "installed" in data or "web" in data
    except Exception:
        raise HTTPException(400, "Not a Desktop-app OAuth client file (it should contain an 'installed' section).")
    DATA.mkdir(exist_ok=True)
    CRED.write_bytes(raw)
    try:
        os.chmod(CRED, 0o600)
    except OSError:
        pass
    return {"ok": True}


def _run_flow():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(CRED), SCOPES)
        creds = flow.run_local_server(port=0, timeout_seconds=240, open_browser=True)
        TOKEN.write_text(creds.to_json())
        _flow["error"] = ""
    except Exception as e:
        _flow["error"] = str(e)
    finally:
        _flow["running"] = False


@router.post("/gmail/connect")
def gmail_connect():
    if not CRED.exists():
        raise HTTPException(400, "Upload your Google OAuth client file first.")
    if PUBLIC_URL:  # hosted: classic web redirect flow, returns to /api/gmail/callback
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(str(CRED), SCOPES, redirect_uri=PUBLIC_URL + "/api/gmail/callback")
        url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
        _pending[state] = flow
        return {"url": url}
    if not _flow["running"]:
        _flow.update(running=True, error="")
        threading.Thread(target=_run_flow, daemon=True).start()
    return {"started": True}


@router.get("/gmail/callback")
def gmail_callback(state: str = "", code: str = "", error: str = ""):
    flow = _pending.pop(state, None)
    if not flow or error or not code:
        _flow["error"] = error or "Sign-in was cancelled or expired. Try Connect again."
        return RedirectResponse("/#mail")
    try:
        flow.fetch_token(code=code)
        TOKEN.write_text(flow.credentials.to_json())
        try:
            os.chmod(TOKEN, 0o600)
        except OSError:
            pass
        _flow["error"] = ""
    except Exception as e:
        _flow["error"] = str(e)
    return RedirectResponse("/#mail")


@router.post("/gmail/disconnect")
def gmail_disconnect():
    TOKEN.unlink(missing_ok=True)
    return {"ok": True}


def split_subject(text):
    mt = text.lstrip().split("\n", 1)
    if mt and mt[0].lower().startswith("subject:"):
        return mt[0][8:].strip(), (mt[1] if len(mt) > 1 else "").lstrip("\n")
    return "", text


def raw_message(to, subject, body):
    msg = MIMEText(body, "plain", "utf-8")
    if to:
        msg["To"] = to
    msg["Subject"] = subject
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


class MailIn(BaseModel):
    professor_id: int
    action: str  # draft | send
    confirm: bool = False


@router.post("/professors/{pid}/gmail")
def professor_gmail(pid: int, b: MailIn):
    """Save the drafted email to Gmail Drafts, or send it. Sending needs an explicit confirm from the UI."""
    with db.conn() as c:
        p = c.execute("SELECT * FROM professors WHERE id=?", (pid,)).fetchone()
        lt = c.execute("SELECT content FROM letters WHERE id=?", (p["letter_id"],)).fetchone() if p and p["letter_id"] else None
    if not p or not lt:
        raise HTTPException(400, "Draft the email first.")
    subject, body = split_subject(lt["content"])
    svc = gmail_service()
    if b.action == "send":
        if not b.confirm:
            raise HTTPException(400, "Sending needs confirmation.")
        if not p["email"]:
            raise HTTPException(400, "No email address for this professor.")
        call(lambda: svc.users().messages().send(userId="me", body={"raw": raw_message(p["email"], subject, body)}).execute())
        with db.conn() as c:
            c.execute("UPDATE professors SET status='sent' WHERE id=?", (pid,))
        return {"sent": True}
    call(lambda: svc.users().drafts().create(userId="me", body={"message": {"raw": raw_message(p["email"] or "", subject, body)}}).execute())
    with db.conn() as c:
        c.execute("UPDATE professors SET status=CASE WHEN status='found' THEN 'drafted' ELSE status END WHERE id=?", (pid,))
    return {"drafted": True}


# =====================================================================
# Mail sync -> status updates
# =====================================================================
RANK = {"new": 0, "interested": 1, "preparing": 2, "submitted": 3, "interview": 4, "waitlisted": 4, "accepted": 5, "rejected": 5}
KIND_STATUS = {"submitted_confirmation": "submitted", "acceptance": "accepted", "rejection": "rejected",
               "waitlist": "waitlisted", "interview": "interview"}


def body_text(payload):
    def walk(p):
        if p.get("mimeType") == "text/plain" and p.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(p["body"]["data"]).decode("utf-8", "ignore")
        for part in p.get("parts") or []:
            t = walk(part)
            if t:
                return t
        return ""
    return walk(payload)


def host_root(url):
    h = urlparse(url or "").netloc.lower().removeprefix("www.")
    return ".".join(h.split(".")[-2:]) if h else ""


def match_message(msg, tracked, profs):
    hay = (msg["from"] + " " + msg["subject"] + " " + msg["body"][:1500]).lower()
    for s in tracked:
        root = host_root(s["url"])
        if (root and root in msg["from"].lower()) or (s["name"] and s["name"].lower() in hay) or \
                (s["provider"] and len(s["provider"]) >= 4 and s["provider"].lower() in hay):
            return True
    for p in profs:
        if (p["email"] and p["email"].lower() in hay) or (p["name"] and p["name"].lower() in hay):
            return True
    return False


@router.post("/gmail/sync")
def gmail_sync():
    svc = gmail_service()
    with db.conn() as c:
        tracked = db.rows(c.execute("SELECT * FROM scholarships WHERE pinned=1"))
        profs = [dict(r) for r in c.execute("SELECT * FROM professors")]
        seen = {r[0] for r in c.execute("SELECT msg_id FROM mail_events")}
    if not tracked:
        raise HTTPException(400, "Track (pin) some scholarships first; only mail about them is read.")
    ids = call(lambda: svc.users().messages().list(userId="me", q="newer_than:90d -category:promotions -category:social",
                                                  maxResults=150).execute()).get("messages", [])
    cands = []
    for ref in ids:
        if ref["id"] in seen:
            continue
        full = call(lambda r=ref: svc.users().messages().get(userId="me", id=r["id"], format="full").execute())
        h = {x["name"].lower(): x["value"] for x in full["payload"].get("headers", [])}
        msg = {"id": ref["id"], "from": h.get("from", ""), "subject": h.get("subject", ""), "date": h.get("date", ""),
               "body": body_text(full["payload"]) or full.get("snippet", "")}
        if match_message(msg, tracked, profs):  # only mail that mentions a tracked scholarship/professor leaves this PC
            cands.append(msg)
    if not cands:
        return {"checked": len(ids), "relevant": 0, "updates": []}
    listing = [{"i": i, "from": x["from"], "subject": x["subject"], "date": x["date"], "body": x["body"][:1800]}
               for i, x in enumerate(cands[:40])]
    prompt = f"""Classify emails about scholarship/university applications. Be conservative.
TRACKED SCHOLARSHIPS: {json.dumps([{"id": s["id"], "name": s["name"], "provider": s["provider"]} for s in tracked])}
TRACKED PROFESSORS: {json.dumps([{"id": p["id"], "name": p["name"], "university": p["university"]} for p in profs])}
EMAILS: {json.dumps(listing)}

For each email return an object: {{"i": index, "scholarship_id": id or null, "professor_id": id or null,
"kind": "submitted_confirmation" | "acceptance" | "rejection" | "waitlist" | "interview" | "documents_requested" | "professor_reply" | "info" | "unrelated",
"summary": "one sentence", "requested_documents": ["only if documents_requested"]}}
Rules: "acceptance" only if the email clearly says the applicant was admitted/awarded. "submitted_confirmation" = application received.
Marketing, newsletters and generic alerts are "unrelated". Return ONLY a JSON array."""
    results = call(lambda: ai.parse_json(ai.generate(prompt, temperature=0.0)))
    updates = []
    with db.conn() as c:
        for r in results:
            try:
                msg = cands[int(r["i"])]
            except (KeyError, ValueError, IndexError):
                continue
            kind, sid, pid = r.get("kind", "unrelated"), r.get("scholarship_id"), r.get("professor_id")
            c.execute("INSERT OR IGNORE INTO mail_events(msg_id,scholarship_id,professor_id,kind,summary,subject,sender,mail_date) "
                      "VALUES(?,?,?,?,?,?,?,?)", (msg["id"], sid, pid, kind, r.get("summary", ""), msg["subject"], msg["from"], msg["date"]))
            if kind in ("unrelated", "info"):
                continue
            if kind == "professor_reply" and pid:
                c.execute("UPDATE professors SET status='replied' WHERE id=?", (pid,))
            if sid and kind in KIND_STATUS:
                cur = c.execute("SELECT status,steps_done FROM scholarships WHERE id=?", (sid,)).fetchone()
                if cur and RANK.get(KIND_STATUS[kind], 0) >= RANK.get(cur["status"], 0):
                    steps = set(json.loads(cur["steps_done"] or "[]"))
                    steps.add("submit")
                    if kind in ("acceptance", "rejection"):
                        steps.add("result")
                    c.execute("UPDATE scholarships SET status=?, steps_done=? WHERE id=?",
                              (KIND_STATUS[kind], json.dumps(sorted(steps)), sid))
            if sid and kind == "documents_requested":
                for d in r.get("requested_documents") or []:
                    add_todo(c, sid, d, "document", "email")
            updates.append({"kind": kind, "summary": r.get("summary", ""), "scholarship_id": sid})
    return {"checked": len(ids), "relevant": len(cands), "updates": [u for u in updates if u["kind"] not in ("unrelated", "info")]}


# =====================================================================
# To-dos pulled from the official page
# =====================================================================
def add_todo(c, sid, text, kind="document", source="auto", detail=""):
    text = (text or "").strip()
    if not text:
        return False
    if c.execute("SELECT 1 FROM todos WHERE scholarship_id=? AND lower(text)=lower(?)", (sid, text)).fetchone():
        return False
    c.execute("INSERT INTO todos(scholarship_id,text,kind,source,detail) VALUES(?,?,?,?,?)", (sid, text, kind, source, detail))
    return True


REQ_PROMPT = """Today is {today}. Extract EVERYTHING an applicant must do to apply to: {name} ({provider}).
Use the official page content and/or Google Search results. Return ONLY JSON:
{{"documents": [{{"text": "short document name, e.g. 'Passport copy'", "detail": "format/length/limits if stated"}}],
 "steps": [{{"text": "action, e.g. 'Create account on the application portal'", "detail": ""}}],
 "open_date": "YYYY-MM-DD or ''", "deadline": "YYYY-MM-DD or ''"}}
Include every document (transcripts, CV, letters, references, language tests, essays, portfolio, ID, photos, fees...)
and every step (portal registration, form, upload, fee payment, interview booking, submission). Never invent items.
{page}"""


def pull_requirements(sid):
    """Reads the scholarship's official page (or searches the web) and fills its to-do list."""
    with db.conn() as c:
        s = db.row(c.execute("SELECT * FROM scholarships WHERE id=?", (sid,)).fetchone())
    if not s:
        return 0
    page = ""
    if s["url"]:
        try:
            page = m().fetch_page(s["url"])[0]
        except Exception:
            page = ""
    prompt = REQ_PROMPT.format(today=today(), name=s["name"], provider=s["provider"],
                               page=("OFFICIAL PAGE TEXT:\n" + page[:25000]) if page else "No page available: search the web.")
    if len(page) >= 800:
        res = ai.parse_json(ai.generate(prompt, temperature=0.1))
    else:
        res = ai.parse_json(agents.search_text(prompt, 0.1)[0])
    added = 0
    with db.conn() as c:
        for d in res.get("documents") or []:
            added += add_todo(c, sid, d.get("text"), "document", "auto", d.get("detail", ""))
        for st in res.get("steps") or []:
            added += add_todo(c, sid, st.get("text"), "step", "auto", st.get("detail", ""))
        c.execute("UPDATE scholarships SET required_docs=? WHERE id=?",
                  (json.dumps([d.get("text") for d in res.get("documents") or [] if d.get("text")]), sid))
        for k in ("open_date", "deadline"):
            if res.get(k) and not s[k]:
                c.execute(f"UPDATE scholarships SET {k}=? WHERE id=?", (res[k], sid))
    return added


def pull_safely(sid):
    try:
        pull_requirements(sid)
    except Exception:
        pass


@router.post("/scholarships/{sid}/requirements")
def requirements(sid: int):
    return {"added": call(pull_requirements, sid)}


class TodoIn(BaseModel):
    text: str
    kind: str = "step"


@router.get("/scholarships/{sid}/todos")
def list_todos(sid: int):
    return todos_with_coverage(sid)


@router.post("/scholarships/{sid}/todos")
def new_todo(sid: int, b: TodoIn):
    with db.conn() as c:
        add_todo(c, sid, b.text, b.kind, "manual")
    return {"ok": True}


@router.patch("/todos/{tid}")
def toggle_todo(tid: int, body: dict):
    with db.conn() as c:
        c.execute("UPDATE todos SET done=? WHERE id=?", (int(bool(body.get("done"))), tid))
    return {"ok": True}


@router.delete("/todos/{tid}")
def delete_todo(tid: int):
    with db.conn() as c:
        c.execute("DELETE FROM todos WHERE id=?", (tid,))
    return {"ok": True}


# ---- covering documents from what the user already uploaded ----
COVER = [("cv", ("cv", "curriculum", "resume", "résumé")), ("transcript", ("transcript", "grade", "academic record")),
         ("certificate", ("certificate", "ielts", "toefl", "language", "diploma", "degree")),
         ("recommendation", ("recommendation", "reference", "referee")),
         ("essay", ("motivation", "statement", "essay", "letter of intent", "personal statement"))]


def covered_by(text, uploaded_kinds, has_letter):
    t = text.lower()
    for kind, words in COVER:
        if any(w in t for w in words):
            if kind == "essay":
                return has_letter or kind in uploaded_kinds
            return kind in uploaded_kinds
    return False


def todos_with_coverage(sid=None):
    with db.conn() as c:
        kinds = {r[0] for r in c.execute("SELECT DISTINCT kind FROM documents")}
        q = "SELECT * FROM todos" + (" WHERE scholarship_id=?" if sid else "") + " ORDER BY kind DESC, id"
        rows = [dict(r) for r in c.execute(q, (sid,) if sid else ())]
        letters = {r[0] for r in c.execute("SELECT DISTINCT scholarship_id FROM letters WHERE kind='motivation'")}
    for t in rows:
        t["covered"] = (not t["done"]) and t["kind"] == "document" and covered_by(t["text"], kinds, t["scholarship_id"] in letters)
    return rows


# =====================================================================
# Dashboard
# =====================================================================
@router.get("/dashboard")
def dashboard():
    import pipeline
    apps = pipeline.applications()
    todos = todos_with_coverage()
    by_s = {}
    for t in todos:
        by_s.setdefault(t["scholarship_id"], []).append(t)
    names = {a["id"]: a for a in apps}
    missing = {}
    for t in todos:
        a = names.get(t["scholarship_id"])
        if not a or t["kind"] != "document" or t["done"] or t["covered"]:
            continue
        key = t["text"].strip().lower()
        e = missing.setdefault(key, {"text": t["text"], "for": []})
        e["for"].append({"id": a["id"], "name": a["name"], "deadline": a["deadline"], "todo_id": t["id"]})
    for a in apps:
        mine = by_s.get(a["id"], [])
        a["todos_total"] = len(mine)
        a["todos_open"] = sum(1 for t in mine if not t["done"] and not t["covered"])
    upcoming = [a for a in apps if a["days_left"] is not None and a["days_left"] >= 0
                and a["status"] not in ("submitted", "accepted", "rejected", "skipped")]
    nxt = min(upcoming, key=lambda a: a["deadline"]) if upcoming else None
    with db.conn() as c:
        events = [dict(r) for r in c.execute(
            "SELECT e.*, s.name sname FROM mail_events e LEFT JOIN scholarships s ON s.id=e.scholarship_id "
            "WHERE e.kind NOT IN ('unrelated','info') ORDER BY e.rowid DESC LIMIT 8")]
    return {"next": nxt, "apps": apps, "missing": sorted(missing.values(), key=lambda e: -len(e["for"])),
            "events": events, "gmail": gmail_status()["connected"]}


# =====================================================================
# Always-on watcher: checks Gmail on a timer and updates statuses
# =====================================================================
_watch = {"interval_min": 0, "last_run": "", "result": "not started"}


def _watch_loop(minutes):
    time.sleep(45)
    while True:
        try:
            if gmail_creds():
                r = gmail_sync()
                _watch["result"] = f"{r['relevant']} relevant emails, {len(r['updates'])} updates"
            else:
                _watch["result"] = "Gmail not connected"
        except HTTPException as e:
            _watch["result"] = f"skipped: {e.detail}"
        except Exception as e:
            _watch["result"] = f"error: {e}"
        _watch["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        time.sleep(minutes * 60)


def start_watcher():
    minutes = int(os.getenv("WATCH_INTERVAL_MIN", "0") or 0)
    _watch["interval_min"] = minutes
    if minutes > 0:
        threading.Thread(target=_watch_loop, args=(minutes,), daemon=True).start()


@router.get("/watcher")
def watcher_status():
    return _watch
