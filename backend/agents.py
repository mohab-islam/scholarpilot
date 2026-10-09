"""Multi-agent helpers: each 'agent' is a focused prompt (usually Google-grounded) run in parallel."""
from concurrent.futures import ThreadPoolExecutor

from google.genai import types

import ai


def search_text(prompt, temperature=0.3):
    """Google-grounded generation. Returns (text, [{title,url}])."""
    cfg = types.GenerateContentConfig(temperature=temperature,
                                      tools=[types.Tool(google_search=types.GoogleSearch())])
    resp = ai.client().models.generate_content(model=ai.MODEL, contents=prompt, config=cfg)
    sources = []
    try:
        for ch in resp.candidates[0].grounding_metadata.grounding_chunks or []:
            if ch.web and ch.web.uri:
                sources.append({"title": ch.web.title or "", "url": ch.web.uri})
    except Exception:
        pass
    return resp.text or "", sources


def run_parallel(jobs, workers=5):
    """jobs: list of (name, callable). Returns {name: result or Exception}."""
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {n: ex.submit(f) for n, f in jobs}
        for n, f in futs.items():
            try:
                out[n] = f.result()
            except Exception as e:
                out[n] = e
    return out


PLAYBOOK_AGENTS = {
    "Structure & formula": "Find the best-regarded paragraph-by-paragraph structures/formulas for a master's scholarship "
                           "motivation letter (opening, academic path, why this programme/country, contribution, close), "
                           "typical word counts, and annotated strong examples.",
    "Selection committee view": "Find what scholarship selection committees and reviewers say they look for in motivation "
                                "letters (rubrics, scoring criteria, red flags, common reasons for rejection) from official "
                                "scholarship sites, university admissions pages and reviewer interviews.",
    "Specific & authentic voice": "Find expert advice on making a motivation letter specific and authentic: strong opening "
                                  "techniques, using concrete moments and evidence, cliches to avoid, how to sound like "
                                  "yourself rather than a template.",
    "Professor outreach": "Find advice from professors and admissions staff on cold emails to prospective supervisors for "
                          "master's/PhD positions: structure, length, what makes them reply, what gets ignored, follow-up timing.",
    "Field and country specifics": None,  # filled with profile at run time
}


def run_playbook(profile_summary):
    jobs = []
    for name, brief in PLAYBOOK_AGENTS.items():
        if brief is None:
            brief = (f"For this applicant: {profile_summary}. Find how motivation letters and scholarship expectations "
                     "differ for their target field and target countries/funders (e.g. DAAD, Chevening, Erasmus Mundus, "
                     "Fulbright, MEXT, CSC): tone, emphasis, and any unwritten expectations.")
        prompt = (f"Research task using Google Search across the whole web (official sites, university pages, "
                  f"credible advisors).\n{brief}\n\nReturn 8-12 concise, actionable bullet points in markdown. "
                  f"Prefer consensus advice backed by multiple sources; do not pad.")
        jobs.append((name, lambda p=prompt: search_text(p)))
    return run_parallel(jobs)


def sweep_queries(level, nationality, field):
    who = f"for applicants from {nationality}" if nationality else "for international applicants"
    fld = f" in {field}" if field else ""
    angles = [
        f"fully funded {level} scholarships{fld} in Europe {who}, currently open or opening soon",
        f"fully funded {level} scholarships{fld} in Asia and Oceania {who}",
        f"{level} scholarships{fld} in North America {who}",
        f"{level} scholarships{fld} in the Middle East and Africa {who}",
        f"government-funded {level} scholarship programmes open now (Chevening, DAAD, Fulbright, MEXT, CSC, Erasmus Mundus, Swiss, Swedish Institute, Australia Awards, etc.) {who}",
        f"university-specific {level} scholarships{fld} with deadlines in the next 6 months {who}",
    ]
    return angles


UNI_RESEARCH_PROMPT = """Today is {today}. Research this opportunity with Google Search, using the official university/department
sites: {target}
Applicant profile: {profile}

Return ONLY one JSON object:
{{"program": {{"name": "", "url": "", "summary": "what the programme is and its focus", "admission_requirements": ["..."], "fit_notes": "how the applicant's background fits"}},
  "labs": [{{"name": "", "url": "", "focus": ""}}],
  "professors": [{{"name": "", "title": "", "university": "", "department": "", "lab": "", "research_interests": "",
                  "recent_work": "one specific recent paper/project if you can verify it, else ''", "email": "", "homepage": "",
                  "why_fit": "1 sentence tying their work to this applicant"}}]}}
Rules: list 5-8 professors whose research best matches the applicant. Put an email ONLY if it is publicly listed on an
official university page you found; otherwise ''. Never guess or construct email addresses. Never invent papers."""


def research_university(target, profile, today):
    text, sources = search_text(UNI_RESEARCH_PROMPT.format(target=target, profile=profile, today=today), 0.1)
    return ai.parse_json(text), sources


def profile_from_text(text, existing_keys):
    keys = ", ".join(k for k, *_ in ai.PROFILE_FIELDS)
    prompt = f"""Extract an applicant profile from the text below (a CV and/or the applicant's own LinkedIn profile).
Return ONLY a JSON object using these keys (omit keys with no evidence; never invent): {keys}.
Write each value as plain concise text (education: degree, institution, years, grades if present; etc.).
TEXT:
{text[:30000]}"""
    return ai.parse_json(ai.generate(prompt, temperature=0.1))


def eligibility_check(scholarship, profile_text):
    prompt = f"""Compare the applicant to the scholarship's eligibility. Be precise and honest.
SCHOLARSHIP: {scholarship['name']} ({scholarship['provider']}, {scholarship['country']}, {scholarship['level']})
ELIGIBILITY: {scholarship['eligibility'] or 'not recorded'}
REQUIRED DOCS: {', '.join(scholarship['required_docs']) or 'not recorded'}
APPLICANT:
{profile_text}

Return ONLY JSON: {{"verdict": "eligible" | "likely" | "unclear" | "not_eligible",
"met": ["criteria met"], "gaps": ["criteria not met or unknown"], "next_actions": ["concrete steps"]}}"""
    return ai.parse_json(ai.generate(prompt, temperature=0.1))
