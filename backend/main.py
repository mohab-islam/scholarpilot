import difflib
from contextlib import asynccontextmanager
import hashlib
import unicodedata
import os
import io
import json
import re
from urllib.parse import parse_qs, urljoin, urlparse
from datetime import date
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
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
import jobs
import verifier

@asynccontextmanager
async def lifespan(_app):
    inbox.start_watcher()  # background Gmail checks (only when WATCH_INTERVAL_MIN > 0)
    verifier.start()  # checks dates, documents and eligibility of everything discovered automatically
    jobs.start()  # LinkedIn ingest queue and the daily internet sweep
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


def fold(text):
    """Lower-case and strip accents ('Sabancı' -> 'sabanci', 'Spärck' -> 'sparck')."""
    text = (text or "").replace("\u0131", "i").replace("\u0130", "I")
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).lower()


NAME_NOISE = re.compile(r"\b(?:19|20)\d{2}(?:[/-]\d{2,4})?\b|\b(?:scholarships?|fellowships?|programmes?|programs?|awards?|for|the|and|in|of|international|students?|masters?|master's|phd|fully|funded)\b")


def norm_name(n):
    f = fold(n)
    short = re.sub(r"[^a-z0-9]+", "", NAME_NOISE.sub("", f))
    return short or re.sub(r"[^a-z0-9]+", "", f)


NAME_STOP = {"scholarship", "scholarships", "fellowship", "fellowships", "programme", "programmes", "program", "programs",
             "award", "awards", "for", "the", "and", "in", "of", "international", "students", "student", "fully", "funded",
             "master", "masters", "master's", "phd", "degree", "full", "joint", "study", "studies", "to", "at", "a", "on",
             "with", "bin", "de", "du", "van", "von", "al"}
# words that describe a kind of award rather than identify one
GENERIC = {"graduate", "postgraduate", "undergraduate", "research", "taught", "excellence", "merit", "based", "global",
           "foreign", "government", "university", "universite", "universitat", "institute", "school", "college",
           "department", "ministry", "fund", "grant", "grants", "support", "general", "national", "open", "call",
           "applications", "new", "cycle", "intake", "mathematics", "technology", "science", "sciences"}


def tokens_of(text):
    t = re.sub(r"\b(?:19|20)\d{2}(?:[/-]\d{2,4})?\b", " ", fold(text))
    return re.findall(r"[a-z0-9]+", t)


def name_tokens(n):
    return {w for w in tokens_of(n) if w not in NAME_STOP and w not in GENERIC and len(w) > 1}


def aliases(row):
    """Acronyms a record is known by: '(MBZUAI)' in a title, or the initials of the provider's full name."""
    out = set()
    for field in ("name", "provider"):
        txt = row.get(field) or ""
        for m in re.findall(r"\(([A-Za-z0-9&/-]{2,12})\)", txt):
            out.add(fold(m).replace("-", "").replace("/", ""))
        words = [w for w in tokens_of(txt) if w not in ("of", "the", "and", "for", "de", "du")]
        if field == "provider" and len(words) >= 3:  # initials of a provider's full name only (not of a title)
            for skip in (set(), {"bin"}):
                ini = "".join(w[0] for w in words if w not in skip)
                if 3 <= len(ini) <= 8:
                    out.add(ini)
    return out


def identity_tokens(row):
    return name_tokens(row.get("name")) | {a for a in aliases(row) if len(a) >= 3}


SHORTENERS = ("lnkd.in", "bit.ly", "t.co", "tinyurl.com", "ow.ly", "buff.ly", "rebrand.ly", "shorturl.at", "cutt.ly")


def is_short_link(url):
    host = urlparse(url or "").netloc.lower().split(":")[0]
    return any(host == h or host.endswith("." + h) for h in SHORTENERS)


def resolve_url(url, depth=0):
    """Follow link shorteners (LinkedIn's lnkd.in shows an interstitial) to the real page."""
    if not url or depth > 3 or not is_short_link(url):
        return url
    try:
        r = httpx.get(url, follow_redirects=False, timeout=15, headers={"User-Agent": "Mozilla/5.0 ScholarPilot"})
        loc = r.headers.get("location", "")
        if loc.startswith("http"):
            return resolve_url(loc, depth + 1)
        for a in BeautifulSoup(r.text, "html.parser").find_all("a", href=True):
            h = a["href"]
            if h.startswith("http") and "linkedin.com" not in h and not is_short_link(h):
                return h.split("#")[0]
    except Exception:
        pass
    return url


MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
MONTH_RE = "|".join(sorted(list(MONTHS) + [m[:3] for m in MONTHS] + ["sept"], key=len, reverse=True))


def normalize_date(text):
    """Turn whatever a page or model returned into 'YYYY-MM-DD' or ''.
    Exact dates pass through; '2027-01' or 'December 2026' become the last day of that month; for ranges
    ('Dec 2026 - Feb 2027') the latest date is used. Text with no recognisable date becomes ''."""
    t = fold(text).strip()
    if not t:
        return ""
    from calendar import monthrange
    found = []
    for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", t):
        found.append((int(m[1]), int(m[2]), int(m[3])))
    t2 = re.sub(r"\d{4}-\d{2}-\d{2}", " ", t)
    for m in re.finditer(r"(\d{4})-(\d{2})(?!\d)", t2):
        found.append((int(m[1]), int(m[2]), None))
    for m in re.finditer(rf"(?:(\d{{1,2}})(?:st|nd|rd|th)?\s+)?\b({MONTH_RE})[a-z]*\.?,?\s*(?:(\d{{1,2}})(?:st|nd|rd|th)?,?\s*)?(\d{{4}})", t2):
        mon = MONTHS.get(m[2]) or next((v for k, v in MONTHS.items() if k.startswith(m[2][:3])), None)
        if mon:
            found.append((int(m[4]), mon, int(m[1] or m[3]) if (m[1] or m[3]) else None))
    out = []
    for y, mo, d in found:
        if not (1 <= mo <= 12 and 2000 <= y <= 2100):
            continue
        last = monthrange(y, mo)[1]
        out.append((y, mo, min(d, last) if d else last))
    if not out:
        return ""
    y, mo, d = max(out)
    return f"{y:04d}-{mo:02d}-{d:02d}"


def norm_mode(v):
    t = fold(v)
    on, off = "online" in t or "web" in t or "portal" in t, "offline" in t or "post" in t or "in person" in t or "paper" in t
    return "both" if (on and off) or "both" in t else "online" if on else "offline" if off else ""


def norm_fee(v):
    t = (v or "").strip().strip("'\"").strip()
    if fold(t) in ("", "not stated", "n/a", "na", "unknown", "varies", "none stated", "not specified", "-"):
        return ""
    if fold(t) in ("free", "no fee", "none", "no application fee", "0", "nil"):
        return "Free"
    return t[:60]


def canon_url(u):
    p = urlparse((u or "").strip().lower())
    host = p.netloc.removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}" if host else ""


def same_scholarship(a, b):
    """Confident duplicate test (same page, same normalised name, or near-identical identifying words).
    Looser cases are settled by adjudicate_duplicates() during the background pass."""
    ua, ub = canon_url(a.get("url")), canon_url(b.get("url"))
    if ua and ua == ub and "/" in ua:  # same page (a bare home page alone does not prove it is the same programme)
        return True
    if norm_name(a.get("name")) and norm_name(a.get("name")) == norm_name(b.get("name")):
        return True
    ta, tb = identity_tokens(a), identity_tokens(b)
    if not ta or not tb:
        return False
    pa, pb = fold(a.get("provider")).strip(), fold(b.get("provider")).strip()
    provider_ok = not pa or not pb or pa in pb or pb in pa or bool(identity_tokens({"name": pa}) & identity_tokens({"name": pb}))
    return provider_ok and len(ta & tb) / len(ta | tb) >= 0.75


def related(a, b):
    """Could these be the same programme? (shares an identifying word); used to build clusters for adjudication."""
    shared = identity_tokens(a) & identity_tokens(b)
    return any(len(t) >= 4 for t in shared)


def adjudicate_duplicates(cluster):
    """Ask the model which records in a small cluster are the SAME scholarship. Returns lists of ids."""
    listing = "\n".join(f'{r["id"]} | {r["name"]} | {r["provider"]} | {r["country"]} | {r["level"]} | deadline {r["deadline"] or "?"} | {r["url"] or ""}'
                        for r in cluster)
    prompt = f"""These are scholarship records collected from several sources. Group the records that are the SAME scholarship
(same funder and same award), even when they are titled differently, abbreviated, or written with different details.
Do NOT merge:
- a general umbrella scheme and a specific named programme under it (e.g. "Erasmus Mundus" vs one specific Erasmus Mundus master);
- the same scheme name run by different universities;
- different tracks or intakes with different deadlines.
Records (id | name | provider | country | level | deadline | url):
{listing}

Return ONLY JSON: {{"groups": [[id, id], ...]}} listing only groups of 2 or more ids that are the same scholarship."""
    res = ai.parse_json(ai.generate(prompt, temperature=0.0))
    groups = res.get("groups", res) if isinstance(res, dict) else res
    ids = {r["id"] for r in cluster}
    return [[int(i) for i in g if int(i) in ids] for g in groups if isinstance(g, list) and len([i for i in g if int(i) in ids]) >= 2]


def merge_duplicates(use_llm=False):
    """Collapse records that describe the same scholarship (LinkedIn, sweeps, manual adds) into one.
    Confident matches merge automatically; with use_llm=True, records sharing a rare identifying word are
    compared by the model once each. Model calls happen outside any database transaction."""
    with db.conn() as c:
        rows = db.rows(c.execute("SELECT * FROM scholarships"))
    by_id = {r["id"]: r for r in rows}
    parent = {r["id"]: r["id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(y)] = find(x)

    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if find(a["id"]) != find(b["id"]) and same_scholarship(a, b):
                union(a["id"], b["id"])
    if use_llm:
        by_token = {}
        for r in rows:
            for t in identity_tokens(r):
                if len(t) >= 4:
                    by_token.setdefault(t, set()).add(r["id"])
        seen_sets, batches = set(), []
        for t, members in sorted(by_token.items(), key=lambda kv: len(kv[1])):
            fs = frozenset(members)
            if 2 <= len(fs) <= 12 and fs not in seen_sets and len({find(i) for i in fs}) > 1:
                seen_sets.add(fs)
                batches.append(sorted(fs))
        for ids_ in batches[:15]:
            cl = [by_id[i] for i in ids_]
            sig = hashlib.sha1("|".join(f"{r['id']}:{fold(r['name'])}" for r in cl).encode()).hexdigest()
            with db.conn() as c:
                if c.execute("SELECT 1 FROM dedupe_checked WHERE sig=?", (sig,)).fetchone():
                    continue
            try:
                groups_ = adjudicate_duplicates(cl)  # slow: no database connection is held while waiting
            except Exception:
                continue  # try again next round
            for g in groups_:
                for other in g[1:]:
                    union(g[0], other)
            with db.conn() as c:
                c.execute("INSERT OR IGNORE INTO dedupe_checked(sig) VALUES(?)", (sig,))
    groups = {}
    for r in rows:
        groups.setdefault(find(r["id"]), []).append(by_id[r["id"]])
    merged = 0
    with db.conn() as c:
        for g in (g for g in groups.values() if len(g) > 1):
            def richness(r):
                return (bool(r["pinned"]), r["source"] == "manual", bool(r["deadline"]), len(r["required_docs"]),
                        bool(r["url"]) and not is_short_link(r["url"]), -r["id"])
            keeper, *others = sorted(g, key=richness, reverse=True)
            for o in others:
                for col in ("provider", "country", "level", "field", "funding", "url", "open_date", "deadline",
                            "eligibility", "fit", "fit_reason", "application_mode", "application_fee", "confidence"):
                    if (not keeper[col] or (col == "url" and is_short_link(keeper[col]))) and o[col] and not (col == "url" and is_short_link(o[col]) and keeper[col]):
                        try:
                            c.execute(f"UPDATE scholarships SET {col}=? WHERE id=?", (o[col], keeper["id"]))
                            keeper[col] = o[col]
                        except Exception:
                            pass  # unique URL already used elsewhere
                docs = list(dict.fromkeys(keeper["required_docs"] + o["required_docs"]))
                c.execute("UPDATE scholarships SET required_docs=? WHERE id=?", (json.dumps(docs), keeper["id"]))
                keeper["required_docs"] = docs
                if o["verdict"] == "qualified" and keeper["verdict"] != "qualified":
                    c.execute("UPDATE scholarships SET verdict='qualified', verdict_reason='' WHERE id=?", (keeper["id"],))
                    keeper["verdict"] = "qualified"
                if o["pinned"] and not keeper["pinned"]:
                    c.execute("UPDATE scholarships SET pinned=1 WHERE id=?", (keeper["id"],))
                for table in ("todos", "professors", "letters"):
                    c.execute(f"UPDATE {table} SET scholarship_id=? WHERE scholarship_id=?", (keeper["id"], o["id"]))
                c.execute("UPDATE notifications SET ref_id=? WHERE ref_id=?", (keeper["id"], o["id"]))
                c.execute("DELETE FROM scholarships WHERE id=?", (o["id"],))
                merged += 1
    return merged


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
        if not existing and (s.get("name") or url):
            # the same programme under another title or URL ("DAAD 2026" vs "DAAD Scholarship", blog vs official page)
            for r in c.execute("SELECT id,name,url,provider FROM scholarships"):
                if same_scholarship({"name": s.get("name"), "url": url, "provider": s.get("provider")}, dict(r)):
                    existing = r
                    break
        if not existing and source not in ("manual", "research") and not admissible(s):
            return None
        vals = {k: s.get(k) or "" for k in
                ("name", "provider", "country", "level", "field", "funding", "open_date", "deadline", "eligibility",
                 "fit", "fit_reason", "application_mode", "application_fee")}
        vals["deadline"], vals["open_date"] = normalize_date(vals["deadline"]), normalize_date(vals["open_date"])
        vals["application_mode"], vals["application_fee"] = norm_mode(vals["application_mode"]), norm_fee(vals["application_fee"])
        if url and is_short_link(url):
            url = resolve_url(url) or url
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
            "required_docs,eligibility,source,fit,fit_reason,verdict,application_mode,application_fee) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vals["name"], vals["provider"], vals["country"], vals["level"], vals["field"], vals["funding"],
             url, vals["open_date"], vals["deadline"], docs, vals["eligibility"], source, vals["fit"], vals["fit_reason"],
             "qualified" if source in ("manual", "research") else "pending",
             vals["application_mode"], vals["application_fee"]))
        if source not in ("manual", "research"):
            verifier.kick()
        return cur.lastrowid


@app.get("/api/scholarships")
def list_scholarships(view: str = "qualified"):
    """view: qualified (default: open or upcoming, and eligible) | review | rejected | pending | all"""
    with db.conn() as c:
        rows_ = db.rows(c.execute(
            "SELECT * FROM scholarships ORDER BY CASE WHEN deadline='' OR deadline IS NULL THEN 1 ELSE 0 END, deadline"))
    for r in rows_:
        r["phase"] = phase_of(r)
    if view == "all":
        return rows_
    if view == "qualified":  # tracked scholarships always stay visible
        return [r for r in rows_ if r["verdict"] == "qualified" or r.get("pinned")]
    return [r for r in rows_ if r["verdict"] == view and not r.get("pinned")]


@app.get("/api/scholarships/counts")
def scholarship_counts():
    with db.conn() as c:
        rows_ = {r[0]: r[1] for r in c.execute("SELECT verdict, count(*) FROM scholarships WHERE pinned=0 GROUP BY verdict")}
        pinned = c.execute("SELECT count(*) FROM scholarships WHERE pinned=1").fetchone()[0]
    return {"qualified": rows_.get("qualified", 0) + pinned, "review": rows_.get("review", 0),
            "rejected": rows_.get("rejected", 0), "pending": rows_.get("pending", 0),
            "verifying": verifier.STATE["running"], "ingesting": sum(v for k, v in jobs.queue_counts().items() if k in ("pending", "processing"))}


class BulkIn(BaseModel):
    ids: list[int]
    action: str  # track | untrack | skip | delete


@app.post("/api/scholarships/bulk")
def bulk_action(b: BulkIn, bg: BackgroundTasks):
    ids = [int(i) for i in b.ids][:500]
    if not ids:
        return {"changed": 0}
    q = ",".join("?" * len(ids))
    need_todos = []
    with db.conn() as c:
        if b.action == "track":
            for r in db.rows(c.execute(f"SELECT * FROM scholarships WHERE id IN ({q})", ids)):
                steps = sorted(set(r["steps_done"]) | {"shortlist"})
                c.execute("UPDATE scholarships SET pinned=1, verdict='qualified', verdict_reason='', steps_done=? WHERE id=?",
                          (json.dumps(steps), r["id"]))
                if not c.execute("SELECT 1 FROM todos WHERE scholarship_id=?", (r["id"],)).fetchone():
                    need_todos.append(r["id"])
        elif b.action == "untrack":
            c.execute(f"UPDATE scholarships SET pinned=0 WHERE id IN ({q})", ids)
        elif b.action == "skip":
            c.execute(f"UPDATE scholarships SET status='skipped', pinned=0, verdict='rejected', "
                      f"verdict_reason='Skipped by you' WHERE id IN ({q})", ids)
        elif b.action == "delete":
            c.execute(f"DELETE FROM scholarships WHERE id IN ({q})", ids)
        else:
            raise HTTPException(400, "Unknown action")
    if need_todos:
        bg.add_task(inbox.pull_many, need_todos)  # read each official page and build its to-do list
    return {"changed": len(ids), "pulling_requirements": len(need_todos)}


@app.get("/api/stats")
def stats():
    """Numbers behind the dashboard visuals."""
    from datetime import timedelta
    today_ = date.today()
    with db.conn() as c:
        rows_ = db.rows(c.execute("SELECT * FROM scholarships"))
        todos = [dict(r) for r in c.execute(
            "SELECT t.done, t.text, t.scholarship_id FROM todos t JOIN scholarships s ON s.id=t.scholarship_id "
            "WHERE s.pinned=1 AND t.kind='document'")]
    days = [(today_ - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    per_day = {d: 0 for d in days}
    for r in rows_:
        d = (r["created"] or "")[:10]
        if d in per_day and r["source"] not in ("manual",):
            per_day[d] += 1
    qualified = [r for r in rows_ if r["verdict"] == "qualified" or r["pinned"]]
    by_country = {}
    for r in qualified:
        k = (r["country"] or "Other").split(",")[0].strip() or "Other"
        by_country[k] = by_country.get(k, 0) + 1
    upcoming = []
    for r in qualified:
        try:
            dl = date.fromisoformat(r["deadline"])
        except (ValueError, TypeError):
            continue
        n = (dl - today_).days
        if 0 <= n <= 180:
            upcoming.append({"id": r["id"], "name": r["name"], "days": n, "pinned": bool(r["pinned"])})
    upcoming.sort(key=lambda x: x["days"])
    kinds = {t["scholarship_id"] for t in todos}
    return {
        "per_day": [{"day": d, "n": per_day[d]} for d in days],
        "new_week": sum(per_day[d] for d in days[-7:]),
        "funnel": {"found": len(rows_), "verified": sum(1 for r in rows_ if r["verdict"] not in ("pending", "")),
                   "qualified": len(qualified), "tracked": sum(1 for r in rows_ if r["pinned"]),
                   "submitted": sum(1 for r in rows_ if r["status"] in ("submitted", "interview", "waitlisted", "accepted"))},
        "by_country": sorted(({"country": k, "n": v} for k, v in by_country.items()), key=lambda x: -x["n"])[:6],
        "upcoming": upcoming[:14],
        "docs": {"total": len(todos), "ready": sum(1 for t in todos if t["done"]), "scholarships": len(kinds)},
    }


@app.get("/favicon.ico")
def favicon():
    return FileResponse(FRONTEND / "favicon.svg", media_type="image/svg+xml")


def run_audit(fix=False):
    """Consistency check of everything collected. With fix=True the safe problems are repaired."""
    today_ = date.today().isoformat()
    issues, fixed = [], 0
    with db.conn() as c:
        rows = db.rows(c.execute("SELECT * FROM scholarships"))
        ids = {r["id"] for r in rows}
        for r in rows:
            for col in ("deadline", "open_date"):
                v = r[col]
                if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                    iso = normalize_date(v)
                    issues.append({"id": r["id"], "kind": "date_format", "detail": f"{col}={v[:40]!r} -> {iso or 'unknown'}"})
                    if fix:
                        c.execute(f"UPDATE scholarships SET {col}=? WHERE id=?", (iso, r["id"]))
                        fixed += 1
            for col, fn in (("application_mode", norm_mode), ("application_fee", norm_fee)):
                if r[col] != fn(r[col]):
                    issues.append({"id": r["id"], "kind": f"{col}_format", "detail": f"{r[col][:40]!r} -> {fn(r[col])!r}"})
                    if fix:
                        c.execute(f"UPDATE scholarships SET {col}=? WHERE id=?", (fn(r[col]), r["id"]))
                        fixed += 1
            if r["verdict"] == "qualified" and not r["pinned"]:
                if r["deadline"] and r["deadline"] < today_:
                    issues.append({"id": r["id"], "kind": "qualified_but_closed", "detail": f"{r['name'][:50]} closed {r['deadline']}"})
                    if fix:
                        c.execute("UPDATE scholarships SET verdict='rejected', verdict_reason=? WHERE id=?", (f"Closed on {r['deadline']}", r["id"]))
                        fixed += 1
                if r["source"] in ("linkedin", "linkedin-link") and r["open_date"] and r["open_date"] > today_:
                    issues.append({"id": r["id"], "kind": "linkedin_not_open", "detail": f"{r['name'][:50]} opens {r['open_date']}"})
                    if fix:
                        c.execute("UPDATE scholarships SET verdict='rejected', verdict_reason=? WHERE id=?", (f"Not open yet (opens {r['open_date']})", r["id"]))
                        fixed += 1
            if r["pinned"] and r["verdict"] != "qualified":
                issues.append({"id": r["id"], "kind": "tracked_but_hidden", "detail": r["name"][:50]})
                if fix:
                    c.execute("UPDATE scholarships SET verdict='qualified', verdict_reason='' WHERE id=?", (r["id"],))
                    fixed += 1
            if r["url"] and is_short_link(r["url"]):
                issues.append({"id": r["id"], "kind": "short_link", "detail": r["url"][:60]})
        for table in ("todos", "professors"):
            orphans = [x[0] for x in c.execute(f"SELECT id FROM {table} WHERE scholarship_id IS NOT NULL AND scholarship_id NOT IN (SELECT id FROM scholarships)")]
            if orphans:
                issues.append({"id": None, "kind": f"orphan_{table}", "detail": f"{len(orphans)} rows"})
                if fix:
                    c.execute(f"DELETE FROM {table} WHERE id IN ({','.join('?' * len(orphans))})", orphans)
                    fixed += len(orphans)
        ghost = [x[0] for x in c.execute("SELECT id FROM notifications WHERE ref_id IS NOT NULL AND ref_id NOT IN (SELECT id FROM scholarships)")]
        if ghost:
            issues.append({"id": None, "kind": "orphan_notifications", "detail": f"{len(ghost)} rows"})
            if fix:
                c.execute(f"DELETE FROM notifications WHERE id IN ({','.join('?' * len(ghost))})", ghost)
                fixed += len(ghost)
        dup_pairs = [(a["id"], b["id"]) for i, a in enumerate(rows) for b in rows[i + 1:] if same_scholarship(a, b)]
        for x, y in dup_pairs:
            issues.append({"id": x, "kind": "duplicate", "detail": f"{x} and {y} are the same scholarship"})
    if fix and dup_pairs:
        fixed += merge_duplicates()
    return {"issues": issues, "count": len(issues), "fixed": fixed}


@app.get("/api/audit")
def audit():
    return run_audit(False)


@app.post("/api/audit/fix")
def audit_fix():
    return run_audit(True)


@app.post("/api/dedupe")
def dedupe_now(deep: bool = False):
    """Merge duplicates. deep=true also asks the model to settle the uncertain ones."""
    return {"merged": merge_duplicates(use_llm=deep)}


@app.post("/api/verify")
def verify_now():
    """Re-check everything that is still pending (or reset review/rejected ones with {"all": true})."""
    verifier.kick()
    return {"ok": True}


@app.post("/api/verify/reset")
def verify_reset(all: bool = False):
    """Re-check filtered ones, or with all=true everything that was found automatically (double-check pass)."""
    with db.conn() as c:
        if all:
            c.execute("UPDATE scholarships SET verdict='pending' WHERE source NOT IN ('manual','research') AND pinned=0")
        else:
            c.execute("UPDATE scholarships SET verdict='pending' WHERE verdict IN ('review','rejected') AND pinned=0")
    verifier.kick()
    return {"ok": True}


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
               "required_docs", "docs_done", "eligibility", "notes", "status", "application_mode",
               "application_fee"}
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
    url = resolve_url(url)
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


def process_post(p):
    """The AI part for one saved post: extract scholarships, follow its links, remember the result."""
    keys = post_keys(p)

    def mark(verdict):
        with db.conn() as c:
            for k in keys:
                c.execute("INSERT INTO seen_posts(key,verdict) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET verdict=excluded.verdict", (k, verdict))

    try:
        found = [s_ for s_ in ai.extract_scholarships(f"LinkedIn post by {p.author}:\n{p.text}", today()) if s_.get("name")]
        saved = sum(1 for s_ in found if save_scholarship(s_, "linkedin"))
        linked, link_seen = 0, set()
        for u in {clean_url(u) for u in p.links + URL_RE.findall(p.text)}:
            if not is_social(u):
                linked += read_url(u, "linkedin-link", seen=link_seen)
    except Exception:
        with db.conn() as c:  # forget the 'queued' marker so a later run retries this post
            for k in keys:
                c.execute("DELETE FROM seen_posts WHERE key=? AND verdict='queued'", (k,))
        raise
    mark("scholarship" if (saved or linked) else "nothing_usable")


@app.post("/api/linkedin/ingest")
def linkedin_ingest(body: IngestIn):
    """Cheap triage happens here (duplicates, already-seen, non-scholarship keywords); the slow AI work is
    queued and runs on the server, so the extension never waits and nothing is lost if the browser closes."""
    require_core()
    st = {"received": len(body.posts), "duplicates": 0, "already_seen": 0, "not_scholarship": 0, "queued": 0}
    batch_keys = set()
    with db.conn() as c:
        for p in body.posts:
            keys = post_keys(p)
            if any(c.execute("SELECT 1 FROM seen_posts WHERE key=?", (k,)).fetchone() for k in keys):
                st["already_seen"] += 1
                continue
            if any(k in batch_keys for k in keys):
                st["duplicates"] += 1
                continue
            batch_keys.update(keys)
            if not STRONG.search(p.text + " " + " ".join(p.links)):
                st["not_scholarship"] += 1
                verdict = "not_scholarship"
            else:
                c.execute("INSERT INTO ingest_queue(post) VALUES(?)", (p.model_dump_json(),))
                st["queued"] += 1
                verdict = "queued"
            for k in keys:
                c.execute("INSERT OR IGNORE INTO seen_posts(key,verdict) VALUES(?,?)", (k, verdict))
    if st["queued"]:
        jobs.kick_ingest()
    return st


# ---------- notifications, settings and background-job status ----------
@app.get("/api/notifications")
def list_notifications():
    with db.conn() as c:
        items = [dict(r) for r in c.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 40")]
    return {"unread": sum(1 for i in items if not i["read"]), "items": items}


@app.post("/api/notifications/read")
def read_notifications():
    with db.conn() as c:
        c.execute("UPDATE notifications SET read=1 WHERE read=0")
    return {"ok": True}


class SettingsIn(BaseModel):
    notify_email: bool | None = None
    daily_sweep: bool | None = None
    sweep_hour_utc: int | None = None


@app.get("/api/settings")
def get_settings():
    return jobs.settings()


@app.put("/api/settings")
def put_settings(b: SettingsIn):
    if b.notify_email is not None:
        jobs.set_setting("notify_email", "1" if b.notify_email else "0")
    if b.daily_sweep is not None:
        jobs.set_setting("daily_sweep", "1" if b.daily_sweep else "0")
    if b.sweep_hour_utc is not None:
        jobs.set_setting("sweep_hour_utc", max(0, min(23, b.sweep_hour_utc)))
    return jobs.settings()


@app.get("/api/jobs")
def job_status():
    return {**jobs.STATE, "queue": jobs.queue_counts(), "verifying": verifier.STATE["running"],
            "pending_verification": len(verifier.pending_ids(500)), "settings": jobs.settings()}


@app.post("/api/sweep/run-now")
def sweep_now():
    import threading
    require_core()
    threading.Thread(target=jobs.run_daily, daemon=True).start()
    return {"started": True}


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


AVATAR_TYPES = {"avif": "image/avif", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


@app.get("/api/avatar")
def get_avatar():
    for ext, mt in AVATAR_TYPES.items():
        p = db.DATA_DIR / f"avatar.{ext}"
        if p.exists():
            return FileResponse(p, media_type=mt, headers={"Cache-Control": "private, max-age=300"})
    raise HTTPException(404, "No profile picture")


@app.post("/api/avatar")
async def set_avatar(file: UploadFile = File(...)):
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in AVATAR_TYPES:
        raise HTTPException(400, "Use an AVIF, PNG, JPG or WebP image")
    raw = await file.read()
    if len(raw) > 5_000_000:
        raise HTTPException(400, "Image is larger than 5 MB")
    for e in AVATAR_TYPES:
        (db.DATA_DIR / f"avatar.{e}").unlink(missing_ok=True)
    (db.DATA_DIR / f"avatar.{ext}").write_bytes(raw)
    return {"ok": True}


@app.get("/login")
def login_page():
    return auth.login_page()


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
