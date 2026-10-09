import difflib
from contextlib import asynccontextmanager
import hashlib
import os
import io
import json
import re
from urllib.parse import parse_qs, urljoin, urlparse
from datetime import date
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ai
import db
import auth
import inbox
import pipeline

@asynccontextmanager
async def lifespan(_app):
    inbox.start_watcher()  # background Gmail checks (only when WATCH_INTERVAL_MIN > 0)
    yield


app = FastAPI(title="ScholarPilot", lifespan=lifespan)
# Local-only app; the LinkedIn extension posts from a chrome-extension:// origin.
# Local-only app that can now send email: allow only the Chrome extension cross-origin, and only local Host headers
# (blocks other websites and DNS-rebinding from driving the API).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"] + [h for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h])
app.add_middleware(CORSMiddleware, allow_origin_regex=r"^chrome-extension://.*$", allow_methods=["*"], allow_headers=["*"])
app.middleware("http")(auth.middleware)  # added last = outermost: checks login before anything else runs
db.init()
ai.applicant_provider = lambda: profile_summary()

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def today():
    return date.today().isoformat()


def ai_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as e:  # surface model/auth/parse problems to the UI
        raise HTTPException(502, f"AI error: {e}")


# ---------- profile & documents ----------
class Profile(BaseModel):
    data: dict


@app.get("/api/profile")
def get_profile():
    with db.conn() as c:
        r = c.execute("SELECT data FROM profile WHERE id=1").fetchone()
    return json.loads(r["data"]) if r else {}


@app.put("/api/profile")
def put_profile(p: Profile):
    with db.conn() as c:
        c.execute("INSERT INTO profile(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                  (json.dumps(p.data),))
    return {"ok": True}


def extract_text(name, raw):
    n = name.lower()
    if n.endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(raw)).pages)
    if n.endswith(".docx"):
        import docx
        return "\n".join(p.text for p in docx.Document(io.BytesIO(raw)).paragraphs)
    return raw.decode("utf-8", errors="ignore")


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...), kind: str = "other"):
    text = extract_text(file.filename, await file.read())
    with db.conn() as c:
        cur = c.execute("INSERT INTO documents(name,kind,text) VALUES(?,?,?)", (file.filename, kind, text))
    return {"id": cur.lastrowid, "name": file.filename, "chars": len(text)}


@app.get("/api/documents")
def list_documents():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT id,name,kind,length(text) chars,created FROM documents ORDER BY id DESC")]


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int):
    with db.conn() as c:
        c.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    return {"ok": True}


def applicant_context(max_doc_chars=6000):
    """Everything we know about the applicant, as text for prompts."""
    with db.conn() as c:
        p = c.execute("SELECT data FROM profile WHERE id=1").fetchone()
        docs = c.execute("SELECT name,kind,text FROM documents ORDER BY id").fetchall()
    parts = []
    if p:
        for k, v in json.loads(p["data"]).items():
            if v:
                parts.append(f"{k}: {v}")
    for d in docs:
        parts.append(f"--- Document: {d['name']} ({d['kind']}) ---\n{(d['text'] or '')[:max_doc_chars]}")
    return "\n".join(parts) or "(no applicant data provided yet)"


def profile_summary():
    with db.conn() as c:
        p = c.execute("SELECT data FROM profile WHERE id=1").fetchone()
    if not p:
        return "unspecified"
    return "; ".join(f"{k}: {v}" for k, v in json.loads(p["data"]).items() if v) or "unspecified"


@app.get("/api/profile/status")
def profile_status():
    p = get_profile()
    fields = [{"key": k, "label": l, "group": g, "required": r, "done": bool(str(p.get(k, "")).strip())}
              for k, l, g, r in ai.PROFILE_FIELDS]
    req = [f for f in fields if f["required"]]
    with db.conn() as c:
        kinds = {r[0] for r in c.execute("SELECT DISTINCT kind FROM documents")}
    checklist = [{"label": f"Upload your {k}", "done": k in kinds} for k in ("cv", "transcript", "essay")]
    done = sum(f["done"] for f in req) + sum(c_["done"] for c_ in checklist)
    miss = core_missing()
    return {"core_complete": not miss, "core_missing": miss, "fields": fields, "documents": checklist, "percent": round(100 * done / (len(req) + len(checklist)))}


class CoachIn(BaseModel):
    question: str = ""


@app.post("/api/profile/coach")
def profile_coach(b: CoachIn):
    """AI profile coach: asks the next best questions, or answers e.g. 'which major should I pick?'."""
    missing = [f["label"] for f in profile_status()["fields"] if f["required"] and not f["done"]]
    prompt = f"""You are a scholarship advisor helping an applicant build their profile.
KNOWN APPLICANT DATA:
{applicant_context(3000)}

STILL MISSING: {', '.join(missing) or 'nothing required'}
APPLICANT SAYS: {b.question or '(nothing - they want guidance on what to fill in next)'}

If they asked a question (major choice, country choice, which scholarships suit them), answer concretely using
their data and name 2-4 options with reasons. Then ask at most 3 short questions that fill the biggest gaps.
Plain text, friendly, under 250 words."""
    return {"reply": ai_call(ai.generate, prompt, temperature=0.5)}


# ---------- scholarships ----------
LEVEL_WORDS = {"bachelor": "bachelor", "undergrad": "bachelor", "bsc": "bachelor", "master": "master",
               "msc": "master", "postgrad": "master", "mba": "master", "phd": "phd", "doctor": "phd",
               "postdoc": "postdoc"}


def level_set(text):
    t = (text or "").lower()
    return {v for k, v in LEVEL_WORDS.items() if k in t}


def core_missing():
    p = get_profile()
    labels = {"nationality": "your country", "target_level": "the degree you want (Master's/Bachelor's/PhD)",
              "target_field": "your major/field"}
    return [v for k, v in labels.items() if not str(p.get(k, "")).strip()]


def require_core():
    miss = core_missing()
    if miss:
        raise HTTPException(400, "Complete your profile first. Missing: " + ", ".join(miss))


def phase_of(s):
    """closed | open | upcoming | unknown, from the stored dates."""
    try:
        if s.get("deadline") and date.fromisoformat(s["deadline"]) < date.today():
            return "closed"
    except ValueError:
        pass
    try:
        if s.get("open_date") and date.fromisoformat(s["open_date"]) > date.today():
            return "upcoming"
    except ValueError:
        pass
    return "open" if s.get("deadline") else "unknown"


def admissible(s):
    """Gate for auto-discovered scholarships: right degree level, applicant not ruled out, not already closed."""
    if phase_of(s) == "closed":
        return False
    if (s.get("fit") or "").lower() == "no":
        return False
    want, have = level_set(get_profile().get("target_level")), level_set(s.get("level"))
    if want and have and not (want & have):
        return False
    return True


NAME_NOISE = re.compile(r"\b(?:19|20)\d{2}(?:[/-]\d{2,4})?\b|\b(?:scholarships?|fellowships?|programmes?|programs?|awards?|for|the|and|in|of|international|students?|masters?|master's|phd|fully|funded)\b")


def norm_name(n):
    short = re.sub(r"[^a-z0-9]+", "", NAME_NOISE.sub("", (n or "").lower()))
    return short or re.sub(r"[^a-z0-9]+", "", (n or "").lower())


def save_scholarship(s, source):
    """Insert or update by url (or name when no url). Returns id, or None if filtered out
    (closed, wrong degree level, or the applicant is not eligible)."""
    url = (s.get("url") or "").strip() or None
    with db.conn() as c:
        existing = None
        if url:
            existing = c.execute("SELECT id FROM scholarships WHERE url=?", (url,)).fetchone()
        if not existing and s.get("name"):
            existing = c.execute("SELECT id FROM scholarships WHERE lower(name)=lower(?)", (s["name"],)).fetchone()
            if not existing:  # same scholarship under a slightly different title ("DAAD 2026" vs "DAAD Scholarship")
                key = norm_name(s["name"])
                for r in c.execute("SELECT id,name FROM scholarships"):
                    if key and norm_name(r["name"]) == key:
                        existing = r
                        break
        if not existing and source not in ("manual", "research") and not admissible(s):
            return None
        vals = {k: s.get(k) or "" for k in
                ("name", "provider", "country", "level", "field", "funding", "open_date", "deadline", "eligibility",
                 "fit", "fit_reason")}
        docs = json.dumps([d for d in (s.get("required_docs") or []) if isinstance(d, str)])
        if existing:
            sets = ", ".join(f"{k}=?" for k in vals if vals[k])
            if sets:
                c.execute(f"UPDATE scholarships SET {sets} WHERE id=?",
                          [v for v in vals.values() if v] + [existing["id"]])
            if docs != "[]":
                c.execute("UPDATE scholarships SET required_docs=? WHERE id=?", (docs, existing["id"]))
            return existing["id"]
        cur = c.execute(
            "INSERT INTO scholarships(name,provider,country,level,field,funding,url,open_date,deadline,"
            "required_docs,eligibility,source,fit,fit_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vals["name"], vals["provider"], vals["country"], vals["level"], vals["field"], vals["funding"],
             url, vals["open_date"], vals["deadline"], docs, vals["eligibility"], source, vals["fit"], vals["fit_reason"]))
        return cur.lastrowid


@app.get("/api/scholarships")
def list_scholarships(show_closed: bool = False):
    with db.conn() as c:
        rows_ = db.rows(c.execute(
            "SELECT * FROM scholarships ORDER BY CASE WHEN deadline='' OR deadline IS NULL THEN 1 ELSE 0 END, deadline"))
    for r in rows_:
        r["phase"] = phase_of(r)
    # tracked scholarships always stay visible
    return [r for r in rows_ if show_closed or r["phase"] != "closed" or r.get("pinned")]


class ScholarshipIn(BaseModel):
    name: str
    url: str = ""
    deadline: str = ""
    open_date: str = ""
    provider: str = ""
    required_docs: list[str] = []


@app.post("/api/scholarships")
def add_scholarship(s: ScholarshipIn):
    return {"id": save_scholarship(s.model_dump(), "manual")}


@app.patch("/api/scholarships/{sid}")
def patch_scholarship(sid: int, body: dict):
    allowed = {"name", "provider", "country", "level", "field", "funding", "url", "open_date", "deadline",
               "required_docs", "docs_done", "eligibility", "notes", "status"}
    sets, vals = [], []
    for k, v in body.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if k in db.JSON_COLS else v)
    if sets:
        with db.conn() as c:
            c.execute(f"UPDATE scholarships SET {', '.join(sets)} WHERE id=?", vals + [sid])
    return {"ok": True}


@app.delete("/api/scholarships/{sid}")
def delete_scholarship(sid: int):
    with db.conn() as c:
        c.execute("DELETE FROM scholarships WHERE id=?", (sid,))
    return {"ok": True}


class ExtractIn(BaseModel):
    url: str = ""
    text: str = ""


def clean_url(u):
    """Unwrap LinkedIn redirect links and drop fragments."""
    p = urlparse(u)
    if "linkedin.com" in p.netloc and p.path.startswith("/safety/go"):
        inner = parse_qs(p.query).get("url")
        if inner:
            return clean_url(inner[0])
    return u.split("#")[0]


def fetch_page(url):
    """Returns (text, [(anchor_text, absolute_href)])."""
    r = httpx.get(url, follow_redirects=True, timeout=20, headers={"User-Agent": "Mozilla/5.0 ScholarPilot"})
    soup = BeautifulSoup(r.text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        h = urljoin(str(r.url), a["href"])
        if h.startswith("http"):
            links.append((a.get_text(" ", strip=True), h.split("#")[0]))
    for t in soup(["script", "style", "nav", "footer"]):
        t.decompose()
    return soup.get_text("\n", strip=True), links


def read_url(url, source, depth=0, seen=None, budget=None):
    """Read a URL with the LLM. Single scholarship page -> save it. Aggregator/listing -> follow its
    detail links (one level) and read each, so we capture real requirements, not just the blog summary."""
    seen = seen if seen is not None else set()
    budget = budget if budget is not None else [10]
    url = clean_url(url)
    if url in seen or budget[0] <= 0:
        return 0
    seen.add(url)
    budget[0] -= 1
    try:
        text, links = fetch_page(url)
        res = ai.analyze_page(text, links, url, today())
    except Exception:
        return 0
    if isinstance(res, list):
        res = {"kind": "listing", "scholarships": res, "follow": []}
    saved = 0
    kind = res.get("kind")
    for s_ in res.get("scholarships") or []:
        if not s_.get("name"):
            continue
        if kind == "single" and not s_.get("url"):
            s_["url"] = url
        if kind == "listing" and s_.get("url") == url:
            s_["url"] = ""  # the blog page is not the scholarship's own page
        if save_scholarship(s_, source):
            saved += 1
    if kind == "listing" and depth == 0:
        for link in (res.get("follow") or [])[:8]:
            saved += read_url(link, source, 1, seen, budget)
    return saved


@app.post("/api/scholarships/extract")
def extract(body: ExtractIn):
    text = body.text
    if body.url and not text:
        n = read_url(body.url, "link")
        if not n:
            raise HTTPException(400, "Could not read any scholarship from that URL")
        return {"added": n, "ids": []}
    if not text.strip():
        raise HTTPException(400, "Provide a url or text")
    found = ai_call(ai.extract_scholarships, text, today())
    for s in found:
        if body.url and not s.get("url"):
            s["url"] = body.url
    ids = [i for i in (save_scholarship(s, "extract") for s in found if s.get("name")) if i]
    return {"added": len(ids), "ids": ids}


@app.post("/api/scholarships/{sid}/research")
def research(sid: int):
    """Google-grounded refresh of dates and required documents."""
    with db.conn() as c:
        r = c.execute("SELECT name,url FROM scholarships WHERE id=?", (sid,)).fetchone()
    if not r:
        raise HTTPException(404)
    s = ai_call(ai.research_scholarship, r["name"], r["url"], today())
    if isinstance(s, list):
        s = s[0] if s else {}
    s["name"] = r["name"]
    s["url"] = r["url"] or s.get("url")
    save_scholarship(s, "research")
    return {"ok": True}


class DiscoverIn(BaseModel):
    query: str = ""


@app.post("/api/discover")
def discover(body: DiscoverIn):
    require_core()
    found = ai_call(ai.discover, profile_summary(), body.query, today())
    ids = [i for i in (save_scholarship(s, "google") for s in found if s.get("name")) if i]
    return {"added": len(ids)}


# ---------- LinkedIn ingest (from the browser extension) ----------
class Post(BaseModel):
    url: str = ""
    author: str = ""
    text: str
    links: list[str] = []


class IngestIn(BaseModel):
    posts: list[Post]


URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
STRONG = re.compile(r"scholarship|fellowship|bursar|stipend|fully[- ]funded|funded (?:master|phd|msc|ma\b|program|position|study)|"
                    r"erasmus|chevening|daad|fulbright|commonwealth|mext\b|\bcsc\b|study abroad|tuition[- ]free|full tuition|\u0645\u0646\u062d", re.I)
SOCIAL_HOSTS = ("linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "youtu.be",
                "tiktok.com", "t.me", "wa.me", "whatsapp.com")


def post_keys(p):
    """Stable identities for a saved post: LinkedIn activity id / URL, plus a hash of its normalised text."""
    keys = []
    m = re.search(r"(?:urn:li:activity:|activity-)(\d{8,})", p.url or "")
    if m:
        keys.append("li:" + m.group(1))
    elif p.url:
        keys.append("url:" + p.url.split("?")[0])
    norm = re.sub(r"\W+", " ", p.text.lower()).strip()[:400]
    if norm:
        keys.append("tx:" + hashlib.sha1(norm.encode()).hexdigest())
    return keys


def is_social(url):
    host = urlparse(url).netloc.lower().split(":")[0]
    return not host or any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS)


@app.post("/api/linkedin/reset-seen")
def reset_seen():
    with db.conn() as c:
        c.execute("DELETE FROM seen_posts")
    return {"ok": True}


@app.post("/api/linkedin/ingest")
def linkedin_ingest(body: IngestIn):
    """For each saved post: skip ones already processed or duplicated, skip non-scholarship posts cheaply,
    extract scholarships from the rest, and read the external links they contain (aggregator pages are
    followed to the individual scholarships)."""
    require_core()
    st = {"received": len(body.posts), "duplicates": 0, "already_seen": 0, "not_scholarship": 0,
          "scholarships": 0, "filtered_out": 0, "errors": 0}
    batch_keys, link_seen = set(), set()
    with db.conn() as c:
        n_before = c.execute("SELECT count(*) FROM scholarships").fetchone()[0]
    for p in body.posts:
        keys = post_keys(p)
        with db.conn() as c:
            known = any(c.execute("SELECT 1 FROM seen_posts WHERE key=?", (k,)).fetchone() for k in keys)
        if known:
            st["already_seen"] += 1
            continue
        if any(k in batch_keys for k in keys):
            st["duplicates"] += 1
            continue
        batch_keys.update(keys)

        def mark(verdict):
            with db.conn() as c:
                for k in keys:
                    c.execute("INSERT OR IGNORE INTO seen_posts(key,verdict) VALUES(?,?)", (k, verdict))

        if not STRONG.search(p.text + " " + " ".join(p.links)):
            st["not_scholarship"] += 1
            mark("not_scholarship")
            continue
        try:
            found = [s_ for s_ in ai.extract_scholarships(f"LinkedIn post by {p.author}:\n{p.text}", today()) if s_.get("name")]
        except Exception:
            st["errors"] += 1  # not marked as seen, so a later run retries it
            continue
        saved = sum(1 for s_ in found if save_scholarship(s_, "linkedin"))
        st["filtered_out"] += len(found) - saved
        linked = 0
        for u in {clean_url(u) for u in p.links + URL_RE.findall(p.text)}:
            if not is_social(u):
                linked += read_url(u, "linkedin-link", seen=link_seen)
        mark("scholarship" if (saved or linked) else "nothing_usable")
    with db.conn() as c:  # genuinely new rows only; updates to ones we already had don't count
        st["scholarships"] = c.execute("SELECT count(*) FROM scholarships").fetchone()[0] - n_before
    return st


# ---------- planner ----------
@app.get("/api/plan")
def plan():
    out = []
    now = date.today()
    with db.conn() as c:
        for s in db.rows(c.execute("SELECT * FROM scholarships WHERE status NOT IN ('rejected','skipped','submitted')")):
            if not s["deadline"]:
                continue
            try:
                dl = date.fromisoformat(s["deadline"])
            except ValueError:
                continue
            left = len(s["required_docs"]) - len(s["docs_done"])
            out.append({"id": s["id"], "name": s["name"], "deadline": s["deadline"], "open_date": s["open_date"],
                        "days_left": (dl - now).days, "docs_total": len(s["required_docs"]),
                        "docs_remaining": max(left, 0), "status": s["status"]})
    out.sort(key=lambda x: x["deadline"])
    return out


# ---------- writing ----------
class MotivationIn(BaseModel):
    scholarship_id: int
    angle: str = ""
    word_limit: int = 600
    tone: str = "sincere, confident, reflective"
    extra_answers: str = ""
    template_id: int | None = None


def get_scholarship(sid):
    with db.conn() as c:
        s = db.row(c.execute("SELECT * FROM scholarships WHERE id=?", (sid,)).fetchone())
    if not s:
        raise HTTPException(404, "Scholarship not found")
    return s


def add_version(c, letter_id, content, note):
    last = c.execute("SELECT content,n FROM letter_versions WHERE letter_id=? ORDER BY n DESC LIMIT 1",
                     (letter_id,)).fetchone()
    if last and last["content"] == content:
        return last["n"]
    n = (last["n"] if last else 0) + 1
    c.execute("INSERT INTO letter_versions(letter_id,n,content,note) VALUES(?,?,?,?)", (letter_id, n, content, note))
    return n


def save_letter(sid, kind, title, content):
    with db.conn() as c:
        cur = c.execute("INSERT INTO letters(scholarship_id,kind,title,content) VALUES(?,?,?,?)",
                        (sid, kind, title, content))
        add_version(c, cur.lastrowid, content, "AI draft")
    return {"id": cur.lastrowid, "content": content}


def commit_text(letter_id, text, note):
    if letter_id:
        with db.conn() as c:
            c.execute("UPDATE letters SET content=? WHERE id=?", (text, letter_id))
            add_version(c, letter_id, text, note)
    return {"content": text}


@app.post("/api/write/motivation")
def write_motivation(b: MotivationIn):
    s = get_scholarship(b.scholarship_id)
    prompt = f"""Write a motivation letter (about {b.word_limit} words) for this scholarship.
Tone: {b.tone}. Applicant's chosen angle/story: {b.angle or 'choose the strongest through-line from their data'}.

SCHOLARSHIP: {s['name']} by {s['provider']} ({s['country']}, {s['level']}, {s['field']}).
Funding: {s['funding']}. Eligibility: {s['eligibility']}.

APPLICANT DATA:
{applicant_context()}

EXTRA ANSWERS FROM APPLICANT:
{b.extra_answers or '(none)'}

RESEARCHED BEST PRACTICE (apply it, do not quote it):
{pipeline.playbook_text(4500) or '(none yet)'}

{('FOLLOW THIS TEMPLATE STRUCTURE, filling placeholders from the applicant data:' + chr(10) + pipeline.template_body(b.template_id)) if b.template_id else ''}

{('UNIVERSITY / PROGRAMME RESEARCH:' + chr(10) + s['research']) if s.get('research') else ''}

Structure: a specific opening moment (not a cliche), the intellectual/personal journey with concrete evidence,
why THIS scholarship and destination, what they will contribute, and a forward-looking close tied to the
scholarship's mission."""
    text = ai_call(ai.generate, prompt, system=ai.WRITER_SYSTEM)
    return save_letter(b.scholarship_id, "motivation", f"Motivation letter – {s['name']}", text)


class ProfessorIn(BaseModel):
    scholarship_id: int | None = None
    professor_name: str
    university: str
    research_interests: str = ""
    paper_or_project: str = ""
    goal: str = "PhD supervision"
    as_email: bool = True


@app.post("/api/write/professor")
def write_professor(b: ProfessorIn):
    s = get_scholarship(b.scholarship_id) if b.scholarship_id else None
    prompt = f"""Write a {'cold email' if b.as_email else 'cover letter'} (max 250 words) to Professor
{b.professor_name} at {b.university} asking about: {b.goal}.
Their research interests: {b.research_interests or 'unknown'}.
A specific paper/project of theirs to reference: {b.paper_or_project or 'none given - do not invent one; use a [bracketed placeholder]'}.
{('Mention the funding route: ' + s['name'] + ' (deadline ' + s['deadline'] + ').') if s else ''}

APPLICANT DATA:
{applicant_context(3000)}

Be specific about how the applicant's past work connects to the professor's research, make one clear ask,
and include a subject line first ('Subject: ...')."""
    text = ai_call(ai.generate, prompt, system=ai.WRITER_SYSTEM)
    return save_letter(b.scholarship_id, "professor", f"Prof. {b.professor_name} – {b.university}", text)


class RefineIn(BaseModel):
    content: str
    instruction: str
    letter_id: int | None = None


@app.post("/api/write/refine")
def refine(b: RefineIn):
    text = ai_call(ai.generate, f"Revise the letter below per the instruction. Keep facts unchanged.\n"
                   f"INSTRUCTION: {b.instruction}\n\nLETTER:\n{b.content}", system=ai.WRITER_SYSTEM)
    return commit_text(b.letter_id, text, f"Refine: {b.instruction[:60]}")


class HumanizeIn(BaseModel):
    content: str
    letter_id: int | None = None


def voice_samples():
    with db.conn() as c:
        rs = c.execute("SELECT text FROM documents WHERE kind='essay' ORDER BY id DESC LIMIT 3").fetchall()
        p = c.execute("SELECT data FROM profile WHERE id=1").fetchone()
    samples = [r["text"][:2500] for r in rs]
    if p:
        story = json.loads(p["data"]).get("story")
        if story:
            samples.append(story[:2500])
    return "\n\n".join(samples)


@app.post("/api/write/humanize")
def humanize(b: HumanizeIn):
    scan = ai.heuristic_ai_scan(b.content)
    prompt = f"""Rewrite the letter so it sounds like a real person wrote it, not a template.
- Match the applicant's own voice using the samples of their writing below (rhythm, vocabulary, sentence
  length, formality). With no samples, write plainly, like a thoughtful student, not a copywriter.
- Remove stock phrases and inflated words ({', '.join(scan['stock_phrases']) or 'e.g. passion, journey, tapestry, leverage'}).
- Vary sentence length; a short sentence or fragment is fine where natural. Avoid em dashes and triplet lists.
- Prefer concrete detail already in the letter over abstractions. Do NOT add new facts, numbers or names.
  Where the letter is vague and a real detail is needed, put a [bracketed prompt] such as
  [what exactly did you build in that project?] for the applicant to answer.
- Keep the length within 10% of the original. Output only the letter.

APPLICANT'S OWN WRITING SAMPLES:
{voice_samples() or '(none)'}

LETTER:
{b.content}"""
    text = ai_call(ai.generate, prompt, system=ai.WRITER_SYSTEM, temperature=0.9)
    return commit_text(b.letter_id, text, "Humanize pass")


@app.post("/api/write/check")
def check_authenticity(b: HumanizeIn):
    """Heuristic scan + LLM critique. NOT an AI detector and no guarantee; it points at what to personalise."""
    scan = ai.heuristic_ai_scan(b.content)
    prompt = f"""You are a tough admissions reader. Quote up to 6 sentences from the letter that sound generic,
templated or could appear in anyone's letter, and for each give a one-line question the applicant should
answer to make it specific to their life. Then give an overall verdict (1-2 sentences) on how personal it feels.
Plain text.

LETTER:
{b.content}"""
    return {"scan": scan, "critique": ai_call(ai.generate, prompt, temperature=0.3)}


@app.get("/api/letters/{lid}/versions")
def versions(lid: int):
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id,n,note,created,content FROM letter_versions WHERE letter_id=? ORDER BY n DESC", (lid,))]


@app.get("/api/letters/{lid}/diff")
def diff(lid: int, a: int, b: int):
    with db.conn() as c:
        ra = c.execute("SELECT content FROM letter_versions WHERE letter_id=? AND n=?", (lid, a)).fetchone()
        rb = c.execute("SELECT content FROM letter_versions WHERE letter_id=? AND n=?", (lid, b)).fetchone()
    if not ra or not rb:
        raise HTTPException(404)
    wa, wb = re.findall(r"\S+\s*", ra["content"]), re.findall(r"\S+\s*", rb["content"])
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if op == "equal":
            out.append({"op": "eq", "text": "".join(wa[i1:i2])})
        else:
            if i2 > i1:
                out.append({"op": "del", "text": "".join(wa[i1:i2])})
            if j2 > j1:
                out.append({"op": "ins", "text": "".join(wb[j1:j2])})
    return out


@app.post("/api/letters/{lid}/restore/{n}")
def restore(lid: int, n: int):
    with db.conn() as c:
        r = c.execute("SELECT content FROM letter_versions WHERE letter_id=? AND n=?", (lid, n)).fetchone()
    if not r:
        raise HTTPException(404)
    return commit_text(lid, r["content"], f"Restored v{n}")


@app.post("/api/write/interview/{sid}")
def interview(sid: int):
    """Questions that pull out the unique material a strong letter needs."""
    s = get_scholarship(sid)
    prompt = f"""An applicant is writing a motivation letter for "{s['name']}". Based on what we already know,
ask 6 sharp, specific questions whose answers would give the letter unique, memorable material (concrete
moments, failures, turning points, impact). Skip anything already answered in the data.

KNOWN DATA:
{applicant_context(3000)}

Return ONLY a JSON array of 6 question strings."""
    return {"questions": ai_call(lambda: ai.parse_json(ai.generate(prompt, temperature=0.5)))}


@app.get("/api/letters")
def list_letters():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM letters ORDER BY id DESC")]


class LetterPut(BaseModel):
    content: str


@app.put("/api/letters/{lid}")
def put_letter(lid: int, b: LetterPut):
    commit_text(lid, b.content, "Manual edit")
    return {"ok": True}


@app.delete("/api/letters/{lid}")
def delete_letter(lid: int):
    with db.conn() as c:
        c.execute("DELETE FROM letters WHERE id=?", (lid,))
        c.execute("DELETE FROM letter_versions WHERE letter_id=?", (lid,))
    return {"ok": True}


app.include_router(auth.router)
app.include_router(pipeline.router)
app.include_router(inbox.router)


@app.get("/login")
def login_page():
    return auth.login_page()


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
