import json
import os
import re

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
_client = None


def client():
    global _client
    if _client is None:
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise RuntimeError("Set GOOGLE_CLOUD_PROJECT in .env (see .env.example)")
        _client = genai.Client(
            vertexai=True, project=project,
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
    return _client


def generate(prompt, system=None, search=False, temperature=0.7):
    """Call Gemini on Vertex. search=True enables Google Search grounding."""
    cfg = types.GenerateContentConfig(
        system_instruction=system, temperature=temperature,
        tools=[types.Tool(google_search=types.GoogleSearch())] if search else None)
    resp = client().models.generate_content(model=MODEL, contents=prompt, config=cfg)
    return resp.text or ""


def parse_json(text):
    """Pull the first JSON object/array out of a model reply."""
    text = re.sub(r"```(?:json)?", "", text)
    starts = [i for i in (text.find("["), text.find("{")) if i != -1]
    if not starts:
        raise ValueError("No JSON in model reply")
    start = min(starts)
    end = max(text.rfind("]"), text.rfind("}"))
    return json.loads(text[start:end + 1])


SCHOLARSHIP_FIELDS = """{
  "name": "scholarship name",
  "provider": "organisation/university/government offering it",
  "country": "host country or 'Multiple'",
  "level": "Bachelor | Master | PhD | Postdoc | Any",
  "field": "fields of study or 'Any'",
  "funding": "what is covered (tuition, stipend, travel...)",
  "url": "official application/info page URL",
  "open_date": "YYYY-MM-DD or '' if unknown",
  "deadline": "YYYY-MM-DD or '' if unknown",
  "required_docs": ["every document/step the applicant must submit, one short item each"],
  "eligibility": "short summary of eligibility rules (nationality, level, field, language, age, etc.)",
  "fit": "yes | likely | unclear | no: can THIS applicant (see APPLICANT) apply, judged on nationality, degree level and field?",
  "fit_reason": "one short reason for the fit value"
}"""


applicant_provider = lambda: ""  # set by main: returns a text summary of the applicant profile


def applicant_clause():
    a = applicant_provider()
    return f"\nAPPLICANT (judge `fit` against this): {a}\n" if a and a != "unspecified" else ""


def extract_scholarships(text, today):
    prompt = f"""Today is {today}. From the text below, extract every distinct scholarship/fellowship/funded
programme mentioned. Return ONLY a JSON array of objects with exactly this shape:
{SCHOLARSHIP_FIELDS}
Use '' / [] for anything not stated; never invent dates. If no scholarship is present return [].
{applicant_clause()}

TEXT:
{text[:30000]}"""
    return parse_json(generate(prompt, temperature=0.1))


def research_scholarship(name, url, today):
    """Use Google Search grounding to fill in dates/docs for a known scholarship."""
    prompt = f"""Today is {today}. Research the scholarship "{name}" {('(' + url + ')') if url else ''} using
Google Search, preferring the official website. Find the NEXT upcoming application cycle's opening date and
deadline, and the complete list of required documents. Return ONLY one JSON object shaped:
{SCHOLARSHIP_FIELDS}
Use '' / [] when you cannot verify something; never guess dates.
{applicant_clause()}"""
    return parse_json(generate(prompt, search=True, temperature=0.1))


def discover(profile_summary, query, today):
    prompt = f"""Today is {today}. Using Google Search, find 8-12 real scholarships/funded programmes for this applicant.
APPLICANT: {profile_summary}
EXTRA REQUEST: {query or 'none'}

HARD FILTERS (drop anything that fails them):
1. The applicant must be ELIGIBLE: their nationality/residence is allowed, the degree level matches what they
   want to study, and their field is covered. Check the official eligibility rules, not the headline.
2. Applications must be OPEN NOW (deadline on or after {today}) or OPEN IN THE FUTURE (next cycle with a
   published opening date). Exclude anything whose deadline has passed and has no announced next cycle.
Prefer official sources. Return ONLY a JSON array of objects shaped:
{SCHOLARSHIP_FIELDS}
Use '' / [] when not verifiable; never invent dates."""
    return parse_json(generate(prompt, search=True, temperature=0.2))


WRITER_SYSTEM = """You are an elite admissions writer. You write in the applicant's authentic first-person voice:
specific, concrete, reflective, never generic or cliched. You only use facts present in the applicant data
provided; never fabricate achievements, numbers, publications or names. If a needed fact is missing, insert
a short [bracketed placeholder] for the applicant to fill. Avoid opening with 'Ever since I was a child'
or similar tropes. Output only the letter text, no commentary."""


def analyze_page(page_text, links, url, today):
    """Decide whether a page is one scholarship or an aggregator/listing, and extract accordingly."""
    link_lines = "\n".join(f"- {t[:90]} -> {h}" for t, h in links[:150])
    prompt = f"""Today is {today}. This is the web page at {url}.
Classify it and extract. Return ONLY one JSON object:
{{"kind": "single" | "listing" | "other",
  "scholarships": [ {SCHOLARSHIP_FIELDS} ],
  "follow": ["absolute URLs of pages (from the LINKS list) that are the detail pages of individual scholarships"]}}
- kind "single": the page itself describes one scholarship; put it in scholarships with this page's URL if no better official URL exists; follow = [].
- kind "listing": a blog/aggregator listing many scholarships; put what the listing itself states in scholarships and put up to 8 most relevant individual-scholarship links in follow.
- Never invent dates; use '' / [] when not stated.
{applicant_clause()}
LINKS:
{link_lines}

PAGE TEXT:
{page_text[:25000]}"""
    return parse_json(generate(prompt, temperature=0.1))


PROFILE_FIELDS = [
    # key, label, group, required
    ("full_name", "Full name", "Basics", True),
    ("nationality", "Country / nationality", "Basics", True),
    ("current_level", "Current education level & institution", "Basics", True),
    ("education", "Degrees, GPA/grades, universities", "Academics", True),
    ("target_level", "Level you are applying for (Master/PhD/…)", "Goals", True),
    ("target_field", "Major / field you want", "Goals", True),
    ("target_countries", "Preferred countries", "Goals", False),
    ("start_year", "Intended start (term/year)", "Goals", False),
    ("language_tests", "Language tests (IELTS/TOEFL/other + scores)", "Academics", True),
    ("research_experience", "Research & publications", "Experience", False),
    ("work_experience", "Work / internships", "Experience", False),
    ("achievements", "Awards & achievements", "Experience", False),
    ("leadership", "Leadership & volunteering", "Experience", False),
    ("skills", "Skills", "Experience", False),
    ("story", "Your story: why this field, turning points, long-term goals", "Story", True),
    ("referees", "Possible referees (name, relation)", "Story", False),
]

HUMAN_PATTERNS = [
    "delve", "tapestry", "testament to", "passion for", "ever since i was", "i am writing to express",
    "in today's fast-paced", "rapidly evolving", "multifaceted", "leverage", "synergy", "spearhead",
    "navigate the complexities", "journey", "pivotal", "cutting-edge", "holistic", "unwavering",
    "i am eager to", "i am excited to", "profound", "underscores", "realm of", "stands as",
]


def heuristic_ai_scan(text):
    import statistics
    low = text.lower()
    hits = [p for p in HUMAN_PATTERNS if p in low]
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) > 2]
    lens = [len(s.split()) for s in sents]
    spread = statistics.pstdev(lens) if len(lens) > 2 else 0
    return {
        "stock_phrases": hits,
        "em_dashes": text.count("—"),
        "sentence_count": len(sents),
        "sentence_length_spread": round(spread, 1),
        "uniform_rhythm": bool(lens) and spread < 5,
        "numbers_or_names": len(re.findall(r"\b\d[\d.,%]*\b|\b[A-Z][a-z]+ [A-Z][a-z]+\b", text)),
    }
