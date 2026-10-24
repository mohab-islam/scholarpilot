"""Recommendation letters: saved recommenders, a generic master's-programme draft they can sign, the signed copy,
and the email that asks them to submit it for a specific scholarship."""
import base64
import io
import json
import re
import uuid
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import agents
import ai
import db
import inbox

router = APIRouter(prefix="/api")
SIGNED_DIR = db.DATA_DIR / "recommendations"
ALLOWED = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg"}
MAX_BYTES = 10 * 1024 * 1024
PLAYBOOK_TOPIC = "Recommendation letters"
FIELDS = ("name", "email", "title", "university", "department", "relationship", "course", "course_where",
          "course_year", "achievement", "extra")


def m():
    import main
    return main


class RecIn(BaseModel):
    name: str = ""
    email: str = ""
    title: str = ""
    university: str = ""
    department: str = ""
    relationship: str = ""
    course: str = ""
    course_where: str = ""
    course_year: str = ""
    achievement: str = ""
    extra: str = ""


def one(rid):
    with db.conn() as c:
        r = c.execute("SELECT * FROM recommenders WHERE id=?", (rid,)).fetchone()
    if not r:
        raise HTTPException(404, "Recommender not found")
    return dict(r)


def public(r):
    out = {k: v for k, v in r.items() if k != "signed_file"}
    out["has_draft"] = bool((r.get("draft") or "").strip())
    out["has_signed"] = bool(r.get("signed_file"))
    return out


def sync_referees(c, r):
    """Keep the profile's referee list aware of this person, so every later letter and form can use it."""
    row = c.execute("SELECT data FROM profile WHERE id=1").fetchone()
    data = json.loads(row["data"]) if row else {}
    line = r["name"] + (f", {r['title']}" if r.get("title") else "") + (f", {r['university']}" if r.get("university") else "") \
        + (f" <{r['email']}>" if r.get("email") else "")
    cur = data.get("referees", "")
    key = (r.get("email") or r.get("name") or "").lower()
    if key and key in cur.lower():
        return
    data["referees"] = (cur + "; " if cur.strip() else "") + line
    c.execute("INSERT INTO profile(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (json.dumps(data),))


@router.get("/recommenders")
def list_recommenders():
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM recommenders ORDER BY id DESC")]
    return [public(r) for r in rows]


@router.post("/recommenders")
def create_recommender(b: RecIn):
    if not b.name.strip():
        raise HTTPException(400, "The professor's name is required.")
    d = {k: getattr(b, k).strip() for k in FIELDS}
    with db.conn() as c:
        cur = c.execute(f"INSERT INTO recommenders({','.join(FIELDS)}) VALUES({','.join('?' * len(FIELDS))})", [d[k] for k in FIELDS])
        sync_referees(c, d)
    return public(one(cur.lastrowid))


@router.put("/recommenders/{rid}")
def update_recommender(rid: int, b: RecIn):
    one(rid)
    d = {k: getattr(b, k).strip() for k in FIELDS}
    with db.conn() as c:
        c.execute(f"UPDATE recommenders SET {','.join(k + '=?' for k in FIELDS)} WHERE id=?", [d[k] for k in FIELDS] + [rid])
        sync_referees(c, d)
    return public(one(rid))


@router.delete("/recommenders/{rid}")
def delete_recommender(rid: int):
    r = one(rid)
    if r.get("signed_file"):
        (SIGNED_DIR / r["signed_file"]).unlink(missing_ok=True)
    with db.conn() as c:
        c.execute("DELETE FROM recommenders WHERE id=?", (rid,))
        c.execute("DELETE FROM rec_requests WHERE recommender_id=?", (rid,))
        c.execute("DELETE FROM documents WHERE kind='recommendation' AND name=?", (f"Recommendation - {r['name']}",))
    return {"ok": True}


# ---------- the draft ----------
REC_SYSTEM = """You write recommendation letters that a university lecturer or supervisor will read, adjust and sign.
Write in the recommender's first-person voice. Be specific and credible: what they taught or supervised, where, what
they observed in the student, one concrete achievement, and an honest comparison of the student's strengths.
Use ONLY the facts provided; never invent grades, rankings, percentages, publications or events. If a detail is missing,
leave a short [bracketed placeholder] for the professor to fill. Address it generically ("To the Admissions Committee")
so one letter works for any master's programme or scholarship; do not name a specific programme. Avoid stock praise
("hard-working, passionate, team player") unless backed by an example. Output only the letter, ending with the
recommender's signature block (name, title, department, university, email)."""


def build_prompt(r, instruction=""):
    with db.conn() as c:
        pb = c.execute("SELECT body FROM playbook WHERE topic=?", (PLAYBOOK_TOPIC,)).fetchone()
    p = m().get_profile()
    keys = ("full_name", "nationality", "current_level", "education", "target_level", "target_field",
            "research_experience", "achievements", "skills")
    facts = {k: p.get(k, "") for k in keys if p.get(k)}
    field = p.get("target_field") or "the student's field"
    since = f"in {r['course_year']}" if r.get("course_year") else ""
    change = f"CHANGE REQUEST FROM THE STUDENT: {instruction}" if instruction else ""
    advice = (pb["body"] if pb else "Be concrete, comparative and specific; one story beats five adjectives.")[:3500]
    return f"""Write a recommendation letter for the student below, to be signed by the recommender.

STUDENT (facts from their profile): {json.dumps(facts, ensure_ascii=False)[:2500]}

RECOMMENDER: {r['name']}, {r.get('title') or '[title]'}, {r.get('department') or '[department]'}, {r.get('university') or '[university]'}; email {r.get('email') or '[email]'}.
How they know the student: {r.get('relationship') or '[relationship, e.g. lecturer]'}
Course taught / supervised: {r.get('course') or '[course]'} at {r.get('course_where') or '[institution]'} {since}
Remarkable achievement of the student (from the student): {r.get('achievement') or '[describe one concrete achievement]'}
Other notes: {r.get('extra') or 'none'}
{change}

Target: a letter for a master's programme in {field}, 350-500 words, with these parts:
1) who the recommender is and how/for how long they know the student, 2) the course and how the student performed relative
to peers, 3) the remarkable achievement told as a short concrete story, 4) strengths that matter at master's level
(analysis, independence, communication) tied to evidence, 5) a clear closing recommendation with an offer to be contacted.

BEST-PRACTICE RESEARCH TO APPLY:
{advice}"""


@router.post("/recommenders/{rid}/draft")
def generate_draft(rid: int, body: dict | None = None):
    r = one(rid)
    instruction = (body or {}).get("instruction", "")
    try:
        text = ai.generate(build_prompt(r, instruction), system=REC_SYSTEM, temperature=0.65)
    except Exception as e:
        raise HTTPException(502, f"AI error: {e}")
    with db.conn() as c:
        c.execute("UPDATE recommenders SET draft=?, draft_at=? WHERE id=?", (text.strip(), date.today().isoformat(), rid))
    return {"draft": text.strip()}


class DraftIn(BaseModel):
    draft: str


@router.put("/recommenders/{rid}/draft")
def save_draft(rid: int, b: DraftIn):
    one(rid)
    with db.conn() as c:
        c.execute("UPDATE recommenders SET draft=?, draft_at=? WHERE id=?", (b.draft, date.today().isoformat(), rid))
    return {"ok": True}


def draft_docx(r):
    import docx
    d = docx.Document()
    for para in re.split(r"\n\s*\n", (r.get("draft") or "").strip()):
        d.add_paragraph(para.strip())
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@router.get("/recommenders/{rid}/draft.docx")
def download_draft(rid: int):
    r = one(rid)
    if not (r.get("draft") or "").strip():
        raise HTTPException(400, "No draft yet.")
    name = re.sub(r"[^A-Za-z0-9]+", "_", r["name"]).strip("_") or "professor"
    return Response(draft_docx(r), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="recommendation_{name}.docx"'})


# ---------- the signed letter ----------
@router.post("/recommenders/{rid}/signed")
async def upload_signed(rid: int, file: UploadFile = File(...)):
    r = one(rid)
    fname = file.filename or ""
    ext = ("." + fname.rsplit(".", 1)[-1].lower()) if "." in fname else ""
    if ext not in ALLOWED:
        raise HTTPException(400, "Upload a PDF, Word document or image.")
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(400, "File is larger than 10 MB.")
    SIGNED_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{rid}_{uuid.uuid4().hex[:10]}{ext}"
    (SIGNED_DIR / stored).write_bytes(raw)
    if r.get("signed_file"):
        (SIGNED_DIR / r["signed_file"]).unlink(missing_ok=True)
    try:
        text = m().extract_text(fname, raw) if ext in (".pdf", ".docx") else ""
    except Exception:
        text = ""
    label = f"Recommendation - {r['name']}"
    with db.conn() as c:
        c.execute("UPDATE recommenders SET signed_name=?, signed_file=?, signed_at=? WHERE id=?",
                  (fname, stored, date.today().isoformat(), rid))
        # counts as an uploaded "recommendation" document, so the to-do lists show it as covered
        c.execute("DELETE FROM documents WHERE kind='recommendation' AND name=?", (label,))
        c.execute("INSERT INTO documents(name,kind,text) VALUES(?,?,?)", (label, "recommendation", text))
    return {"ok": True, "name": fname}


@router.get("/recommenders/{rid}/signed")
def download_signed(rid: int):
    r = one(rid)
    path = SIGNED_DIR / (r.get("signed_file") or "none")
    if not r.get("signed_file") or not path.exists():
        raise HTTPException(404, "No signed letter uploaded yet.")
    return FileResponse(path, filename=r.get("signed_name") or path.name)


@router.delete("/recommenders/{rid}/signed")
def delete_signed(rid: int):
    r = one(rid)
    if r.get("signed_file"):
        (SIGNED_DIR / r["signed_file"]).unlink(missing_ok=True)
    with db.conn() as c:
        c.execute("UPDATE recommenders SET signed_name=NULL, signed_file=NULL, signed_at=NULL WHERE id=?", (rid,))
        c.execute("DELETE FROM documents WHERE kind='recommendation' AND name=?", (f"Recommendation - {r['name']}",))
    return {"ok": True}


# ---------- research on what makes a strong letter (feeds the draft) ----------
@router.post("/recommenders/research")
def research_recommendation_advice():
    prompt = ("Research task using Google Search (university admissions pages, scholarship committees, faculty advice).\n"
              "Find what makes a strong recommendation letter for a master's programme or scholarship, written by a professor: "
              "structure, length, what committees look for, how to be specific, comparison to peers, red flags and cliches, "
              "and how professors prefer to be asked and what they need from students. "
              "Return 10-14 concise, actionable markdown bullet points.")
    try:
        text, sources = agents.search_text(prompt)
    except Exception as e:
        raise HTTPException(502, f"AI error: {e}")
    with db.conn() as c:
        c.execute("INSERT INTO playbook(topic,body,sources) VALUES(?,?,?) ON CONFLICT(topic) DO UPDATE SET "
                  "body=excluded.body, sources=excluded.sources, created=CURRENT_TIMESTAMP",
                  (PLAYBOOK_TOPIC, text, json.dumps(sources[:12])))
    return {"ok": True, "sources": len(sources)}


# ---------- per scholarship: does it need a letter, and the email to the professor ----------
def needs_letter(s):
    raw = s.get("required_docs")
    docs = json.loads(raw or "[]") if isinstance(raw, str) else (raw or [])
    texts = [str(d) for d in docs]
    with db.conn() as c:
        texts += [r["text"] for r in c.execute("SELECT text FROM todos WHERE scholarship_id=?", (s["id"],))]
    return any(inbox.doc_label(t) == "Recommendation letters" for t in texts)


def request_email(r, s, attach):
    me = m().get_profile().get("full_name") or "[your name]"
    hello = (r.get("name") or "Professor").strip()
    greet = f"Dear {hello}," if re.match(r"(?i)(prof|dr)\.?\s", hello) else f"Dear Professor {hello},"
    subject = f"Recommendation letter request: {s['name']}"
    lines = [greet, "",
             f"I hope you are well. I am applying to {s['name']}" + (f" ({s['provider']})" if s.get("provider") else "")
             + " and I would be very grateful if you could support my application with a letter of recommendation."]
    if s.get("deadline"):
        lines.append(f"The application deadline is {s['deadline']}, so a signed letter before then would be ideal.")
    if s.get("url"):
        lines.append(f"More about the scholarship: {s['url']}")
    lines += ["",
              ("I have attached a draft based on what we discussed, so you only need to adjust it to your own words and sign it. "
               if attach else "")
              + "If the scholarship's portal sends you a link, please upload the letter there; otherwise you can reply to this "
                "email with the signed letter as a PDF and I will submit it.",
              "", "Thank you very much for your time and support. Please tell me if you need anything more from me.",
              "", "Kind regards,", me]
    return subject, "\n".join(lines)


@router.get("/scholarships/{sid}/recommendations")
def scholarship_recommendations(sid: int):
    with db.conn() as c:
        s = c.execute("SELECT * FROM scholarships WHERE id=?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "Scholarship not found")
        s = dict(s)
        reqs = {r["recommender_id"]: r["status"] for r in c.execute("SELECT * FROM rec_requests WHERE scholarship_id=?", (sid,))}
        recs = [dict(r) for r in c.execute("SELECT * FROM recommenders ORDER BY id DESC")]
    out = []
    for r in recs:
        subj, body = request_email(r, s, bool((r.get("draft") or "").strip()))
        out.append({**public(r), "request_status": reqs.get(r["id"], ""), "subject": subj, "body": body})
    return {"needed": needs_letter(s), "recommenders": out}


class MailReq(BaseModel):
    recommender_id: int
    action: str = "draft"  # draft | send
    subject: str = ""
    body: str = ""
    attach_draft: bool = True
    confirm: bool = False


def build_message(to, subject, body, attachment=None, filename="recommendation_draft.docx"):
    msg = MIMEMultipart()
    if to:
        msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if attachment:
        part = MIMEApplication(attachment, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


@router.post("/scholarships/{sid}/recommendations/email")
def email_recommender(sid: int, b: MailReq):
    """Save the request to Gmail Drafts, or send it. Sending needs an explicit confirm."""
    r = one(b.recommender_id)
    with db.conn() as c:
        s = c.execute("SELECT * FROM scholarships WHERE id=?", (sid,)).fetchone()
    if not s:
        raise HTTPException(404, "Scholarship not found")
    s = dict(s)
    attach = b.attach_draft and bool((r.get("draft") or "").strip())
    d_subj, d_body = request_email(r, s, attach)
    subject, body = b.subject.strip() or d_subj, b.body.strip() or d_body
    if b.action == "send":
        if not b.confirm:
            raise HTTPException(400, "Sending needs confirmation.")
        if not r.get("email"):
            raise HTTPException(400, "No email address saved for this professor.")
    svc = inbox.gmail_service()
    raw = build_message(r.get("email") or "", subject, body, draft_docx(r) if attach else None)
    if b.action == "send":
        inbox.call(lambda: svc.users().messages().send(userId="me", body={"raw": raw}).execute())
        status = "sent"
    else:
        inbox.call(lambda: svc.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute())
        status = "drafted"
    with db.conn() as c:
        c.execute("DELETE FROM rec_requests WHERE recommender_id=? AND scholarship_id=?", (b.recommender_id, sid))
        c.execute("INSERT INTO rec_requests(recommender_id,scholarship_id,status) VALUES(?,?,?)", (b.recommender_id, sid, status))
    return {"status": status}
