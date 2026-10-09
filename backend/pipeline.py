"""Application pipeline: playbook research, templates, sweeps, pinned applications, university/professor research."""
import json
from datetime import date

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

import agents
import ai
import db

router = APIRouter(prefix="/api")

STEPS = [
    ("shortlist", "Shortlist"), ("eligibility", "Eligibility check"), ("documents", "Documents ready"),
    ("research", "University & professor research"), ("outreach", "Professor outreach"),
    ("letter", "Motivation letter"), ("submit", "Submitted"), ("result", "Result"),
]


def m():
    import main  # lazy: main imports this module
    return main


def today():
    return date.today().isoformat()


def call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as e:
        raise HTTPException(502, f"AI error: {e}")


def scholarship(sid):
    with db.conn() as c:
        s = db.row(c.execute("SELECT * FROM scholarships WHERE id=?", (sid,)).fetchone())
    if not s:
        raise HTTPException(404, "Scholarship not found")
    return s


# ---------- playbook (letter formula research) ----------
def playbook_text(limit=7000):
    with db.conn() as c:
        rows = c.execute("SELECT topic,body FROM playbook ORDER BY id").fetchall()
    return "\n\n".join(f"## {r['topic']}\n{r['body']}" for r in rows)[:limit]


@router.post("/playbook/research")
def research_playbook():
    results = call(agents.run_playbook, m().profile_summary())
    saved = 0
    with db.conn() as c:
        for topic, res in results.items():
            if isinstance(res, Exception):
                continue
            body, sources = res
            c.execute("INSERT INTO playbook(topic,body,sources) VALUES(?,?,?) ON CONFLICT(topic) DO UPDATE SET "
                      "body=excluded.body, sources=excluded.sources, created=CURRENT_TIMESTAMP",
                      (topic, body, json.dumps(sources[:12])))
            saved += 1
    if not saved:
        errs = [str(r) for r in results.values() if isinstance(r, Exception)]
        raise HTTPException(502, f"AI error: {errs[0] if errs else 'no results'}")
    return {"topics": saved}


@router.get("/playbook")
def get_playbook():
    with db.conn() as c:
        return [{**dict(r), "sources": json.loads(r["sources"] or "[]")}
                for r in c.execute("SELECT * FROM playbook ORDER BY id")]


# ---------- templates ----------
@router.post("/templates/generate")
def generate_templates():
    prompt = f"""Using the research playbook and the applicant's data, create 3 distinct reusable motivation-letter
TEMPLATES for master's scholarships. Each is a fill-in skeleton: short guidance per paragraph plus
[bracketed placeholders] for facts only the applicant knows. Give each a different strategy
(e.g. research-driven, story-led, impact/leadership-led) chosen to suit this applicant's strengths.

PLAYBOOK:
{playbook_text() or '(none yet - use well-established best practice)'}

APPLICANT:
{m().applicant_context(3000)}

Return ONLY a JSON array of 3 objects: {{"name": "", "description": "when to use it", "body": "the template text"}}"""
    items = call(lambda: ai.parse_json(ai.generate(prompt, temperature=0.6)))
    with db.conn() as c:
        for t in items:
            c.execute("INSERT INTO templates(name,description,body) VALUES(?,?,?)",
                      (t.get("name"), t.get("description"), t.get("body")))
    return {"added": len(items)}


@router.get("/templates")
def list_templates():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM templates ORDER BY id DESC")]


@router.delete("/templates/{tid}")
def delete_template(tid: int):
    with db.conn() as c:
        c.execute("DELETE FROM templates WHERE id=?", (tid,))
    return {"ok": True}


def template_body(tid):
    if not tid:
        return ""
    with db.conn() as c:
        r = c.execute("SELECT body FROM templates WHERE id=?", (tid,)).fetchone()
    return r["body"] if r else ""


# ---------- profile import (CV / LinkedIn profile) ----------
class ImportIn(BaseModel):
    text: str = ""
    source: str = "linkedin_profile"


@router.post("/profile/import")
def import_profile(b: ImportIn):
    """Build/extend the profile from pasted or extension-sent profile text, plus any uploaded CVs.
    Only fills fields that are still empty; never overwrites what the user wrote."""
    text = b.text
    with db.conn() as c:
        cvs = c.execute("SELECT text FROM documents WHERE kind='cv'").fetchall()
    text += "\n\n".join(r["text"] for r in cvs)
    if len(text.strip()) < 50:
        raise HTTPException(400, "No profile text. Upload a CV or send your LinkedIn profile from the extension.")
    extracted = call(agents.profile_from_text, text, None)
    current = m().get_profile()
    filled = []
    for k, v in extracted.items():
        if isinstance(v, list):
            v = "; ".join(str(x) for x in v)
        if v and not str(current.get(k, "")).strip():
            current[k] = str(v)
            filled.append(k)
    m().put_profile(m().Profile(data=current))
    return {"filled": filled}


# ---------- discovery sweep ----------
class SweepIn(BaseModel):
    level: str = ""  # default: the degree level from the profile


@router.post("/sweep")
def sweep(b: SweepIn):
    m().require_core()
    p = m().get_profile()
    queries = agents.sweep_queries(b.level or p.get("target_level", ""), p.get("nationality", ""), p.get("target_field", ""))
    jobs = [(q, lambda q=q: ai.discover(m().profile_summary(), q, today())) for q in queries]
    results = agents.run_parallel(jobs, workers=6)
    added = errors = 0
    for res in results.values():
        if isinstance(res, Exception):
            errors += 1
            continue
        for s in res:
            if s.get("name"):
                if m().save_scholarship(s, "google-sweep"):
                    added += 1
    if errors == len(queries):
        raise HTTPException(502, "AI error: all search agents failed (check Vertex credentials)")
    return {"found": added, "agents": len(queries), "failed": errors}


# ---------- pinned applications ----------
class PinIn(BaseModel):
    pinned: bool = True


@router.post("/scholarships/{sid}/pin")
def pin(sid: int, b: PinIn, bg: BackgroundTasks):
    with db.conn() as c:
        c.execute("UPDATE scholarships SET pinned=? WHERE id=?", (int(b.pinned), sid))
        if b.pinned:
            s = db.row(c.execute("SELECT steps_done FROM scholarships WHERE id=?", (sid,)).fetchone())
            if s and "shortlist" not in s["steps_done"]:
                c.execute("UPDATE scholarships SET steps_done=? WHERE id=?",
                          (json.dumps(s["steps_done"] + ["shortlist"]), sid))
        have_todos = c.execute("SELECT 1 FROM todos WHERE scholarship_id=?", (sid,)).fetchone()
    if b.pinned and not have_todos:
        import inbox
        bg.add_task(inbox.pull_safely, sid)  # read the official page and build the to-do list in the background
    return {"ok": True}


class StepIn(BaseModel):
    step: str
    done: bool


@router.post("/scholarships/{sid}/steps")
def set_step(sid: int, b: StepIn):
    s = scholarship(sid)
    steps = set(s["steps_done"])
    (steps.add if b.done else steps.discard)(b.step)
    with db.conn() as c:
        c.execute("UPDATE scholarships SET steps_done=? WHERE id=?", (json.dumps(sorted(steps)), sid))
    return {"ok": True}


def app_view(s, c):
    """Timeline for one pinned scholarship; some steps complete automatically from real data."""
    pid = s["id"]
    profs = c.execute("SELECT status FROM professors WHERE scholarship_id=?", (pid,)).fetchall()
    docs_t = c.execute("SELECT done FROM todos WHERE scholarship_id=? AND kind='document'", (pid,)).fetchall()
    auto = {
        "documents": (bool(docs_t) and all(t["done"] for t in docs_t)) if docs_t else
        (bool(s["required_docs"]) and len(s["docs_done"]) >= len(s["required_docs"])),
        "research": len(profs) > 0 or bool(s["research"]),
        "outreach": any(p["status"] in ("sent", "replied") for p in profs),
        "letter": c.execute("SELECT 1 FROM letters WHERE scholarship_id=? AND kind='motivation'", (pid,)).fetchone() is not None,
    }
    manual = set(s["steps_done"])
    steps = [{"key": k, "label": l, "done": auto.get(k, False) or k in manual, "auto": k in auto} for k, l in STEPS]
    days = None
    if s["deadline"]:
        try:
            days = (date.fromisoformat(s["deadline"]) - date.today()).days
        except ValueError:
            pass
    nxt = next((x["label"] for x in steps if not x["done"]), "All steps done")
    return {**{k: s[k] for k in ("id", "name", "provider", "country", "deadline", "open_date", "status", "url")},
            "days_left": days, "steps": steps, "next": nxt,
            "progress": round(100 * sum(x["done"] for x in steps) / len(steps))}


@router.get("/applications")
def applications():
    with db.conn() as c:
        rows = db.rows(c.execute("SELECT * FROM scholarships WHERE pinned=1"))
        out = [app_view(s, c) for s in rows]
    out.sort(key=lambda a: (a["days_left"] is None, a["days_left"] if a["days_left"] is not None else 0))
    return out


@router.get("/scholarships/{sid}/workspace")
def workspace(sid: int):
    s = scholarship(sid)
    with db.conn() as c:
        view = app_view(s, c)
        profs = [dict(r) for r in c.execute("SELECT * FROM professors WHERE scholarship_id=? ORDER BY id", (sid,))]
        letters = [dict(r) for r in c.execute(
            "SELECT id,kind,title FROM letters WHERE scholarship_id=? ORDER BY id DESC", (sid,))]
    import inbox
    return {"scholarship": s, "view": view, "professors": profs, "letters": letters, "todos": inbox.todos_with_coverage(sid),
            "steps_def": [{"key": k, "label": l} for k, l in STEPS]}


@router.post("/scholarships/{sid}/eligibility")
def eligibility(sid: int):
    s = scholarship(sid)
    res = call(agents.eligibility_check, s, m().applicant_context(3000))
    with db.conn() as c:
        c.execute("UPDATE scholarships SET eligibility_check=? WHERE id=?", (json.dumps(res), sid))
    return res


# ---------- university / professor research ----------
class UniIn(BaseModel):
    target: str = ""


@router.post("/scholarships/{sid}/university-research")
def university_research(sid: int, b: UniIn):
    s = scholarship(sid)
    target = b.target or f"{s['name']} offered by {s['provider']} ({s['country']}), field: {s['field']}"
    data, sources = call(agents.research_university, target, m().applicant_context(2500), today())
    prog = data.get("program") or {}
    summary = (f"{prog.get('name', '')}\n{prog.get('summary', '')}\nFit: {prog.get('fit_notes', '')}\n"
               f"Admission requirements: {'; '.join(prog.get('admission_requirements') or [])}\n"
               f"Labs: {'; '.join(l.get('name', '') + ' (' + l.get('focus', '') + ')' for l in data.get('labs') or [])}\n"
               f"Sources: {', '.join(x['url'] for x in sources[:6])}")
    n = 0
    with db.conn() as c:
        c.execute("UPDATE scholarships SET research=? WHERE id=?", (summary, sid))
        for p in data.get("professors") or []:
            if not p.get("name"):
                continue
            if c.execute("SELECT 1 FROM professors WHERE scholarship_id=? AND lower(name)=lower(?)",
                         (sid, p["name"])).fetchone():
                continue
            c.execute("INSERT INTO professors(scholarship_id,name,title,university,department,lab,research_interests,"
                      "recent_work,email,homepage,why_fit) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (sid, p.get("name"), p.get("title"), p.get("university"), p.get("department"), p.get("lab"),
                       p.get("research_interests"), p.get("recent_work"), p.get("email"), p.get("homepage"),
                       p.get("why_fit")))
            n += 1
    return {"professors_added": n}


@router.patch("/professors/{pid}")
def patch_professor(pid: int, body: dict):
    allowed = {"status", "email", "homepage", "name"}
    sets = [k for k in body if k in allowed]
    if sets:
        with db.conn() as c:
            c.execute(f"UPDATE professors SET {', '.join(k + '=?' for k in sets)} WHERE id=?",
                      [body[k] for k in sets] + [pid])
    return {"ok": True}


@router.delete("/professors/{pid}")
def delete_professor(pid: int):
    with db.conn() as c:
        c.execute("DELETE FROM professors WHERE id=?", (pid,))
    return {"ok": True}


@router.post("/professors/{pid}/draft")
def draft_professor_email(pid: int):
    """Drafts a cold email grounded in the professor's real research and the applicant's profile.
    The user reviews it and sends it from their own mail client; nothing is sent automatically."""
    with db.conn() as c:
        p = c.execute("SELECT * FROM professors WHERE id=?", (pid,)).fetchone()
    if not p:
        raise HTTPException(404)
    s = scholarship(p["scholarship_id"]) if p["scholarship_id"] else None
    prompt = f"""Write a cold email (max 220 words) from the applicant to Professor {p['name']} ({p['title']},
{p['department']}, {p['university']}), lab: {p['lab'] or 'n/a'}.
Their research: {p['research_interests']}. Recent work to reference: {p['recent_work'] or 'none verified - do not invent; use a [bracketed placeholder]'}.
Why the applicant fits (from research): {p['why_fit']}.
{('Funding route: ' + s['name'] + ' (deadline ' + s['deadline'] + ').') if s else ''}

PLAYBOOK ON COLD EMAILS:
{playbook_text(2500)}

APPLICANT:
{m().applicant_context(3000)}

Start with 'Subject: ...'. One clear, low-effort ask. Specific link between the applicant's past work and the
professor's research. No flattery clichés. Plain, human tone."""
    text = call(ai.generate, prompt, system=ai.WRITER_SYSTEM, temperature=0.8)
    letter = m().save_letter(p["scholarship_id"], "professor", f"Email to Prof. {p['name']}", text)
    with db.conn() as c:
        c.execute("UPDATE professors SET letter_id=?, status=CASE WHEN status='found' THEN 'drafted' ELSE status END "
                  "WHERE id=?", (letter["id"], pid))
    return letter
