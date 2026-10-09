<div align="center">

# 🧭 ScholarPilot

**Your scholarship co-pilot. Find open scholarships you are actually eligible for, track every requirement to the deadline, research universities and professors, and write letters that sound like you.**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-Vertex%20AI-4285F4?logo=googlecloud&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
[![CI](https://github.com/mohab-islam/scholarpilot/actions/workflows/ci.yml/badge.svg)](https://github.com/mohab-islam/scholarpilot/actions)
![License](https://img.shields.io/badge/License-MIT-green)

<img src="docs/screenshots/dashboard.png" alt="ScholarPilot dashboard with a live countdown to the nearest deadline" width="900">

</div>

---

## Why

Applying for scholarships means juggling dozens of tabs: saved LinkedIn posts, aggregator blogs, official pages with different deadlines and document lists, professors to email, and one motivation letter you rewrite ten times. ScholarPilot puts all of it in one calm workspace and lets AI agents do the tedious reading.

## ✨ Features

| | |
|---|---|
| 🔎 **Find** | Parallel search agents sweep the web (Gemini + Google Search grounding) for scholarships matching your **country, degree and field**. Closed ones, wrong degree levels and ones you are not eligible for are filtered out automatically. |
| 💼 **LinkedIn → scholarships** | A Chrome extension scrolls your *saved posts*, removes duplicates, skips posts that are not about scholarships, and follows the links inside them. Aggregator pages are followed to each scholarship's own page. |
| 📋 **Requirements → to-do list** | Track a scholarship and the app reads its official page and builds a checklist of documents and steps. Documents you already uploaded count as covered; the dashboard shows everything still **missing** across all applications. |
| ⏳ **Dashboard** | A live countdown to the nearest deadline, a timeline per application (shortlist → eligibility → documents → professors → outreach → letter → submit → result) and the inbox updates. |
| 🎓 **University and professors** | A research agent finds the programme, labs and well-matched professors with their public pages and email addresses (only from official pages; never guessed), then drafts a specific cold email. |
| ✍️ **Letter lab** | Five research agents collect what makes a strong motivation letter (structure, committee criteria, voice, cold-email advice). Templates, an interview that draws out your real stories, **version history with diffs**, a "sound like me" rewrite based on your own writing, and an authenticity check that flags generic sentences. |
| ✉️ **Gmail** | Drafts and (after explicit confirmation) sends emails; a background watcher reads mail about the scholarships you track and **updates statuses automatically** (received, interview, accepted, rejected) and turns document requests into to-dos. |
| 🔐 **Private by design** | Single-user, your data stays in your own SQLite file and your own Google Cloud project. Hosted mode adds a login, HTTPS and strict host/origin checks. |

<table>
  <tr>
    <td><img src="docs/screenshots/workspace.png" alt="Application workspace"><br><sub>Application workspace</sub></td>
    <td><img src="docs/screenshots/scholarships.png" alt="Scholarship list"><br><sub>Scholarships with fit, phase and tracking</sub></td>
  </tr>
</table>

## 🚀 Quick start (local)

Requirements: Python 3.11+, a Google Cloud project with the **Vertex AI API** enabled (only needed for the AI features).

```bash
git clone https://github.com/mohab-islam/scholarpilot.git
cd scholarpilot

cp .env.example .env            # set GOOGLE_CLOUD_PROJECT
gcloud auth application-default login

./start.sh                      # macOS / Linux   (Windows: start.bat)
# → http://127.0.0.1:8000
```

The first screen asks for your country, the degree you want and your major. Nothing else works until your profile is set, because every search is filtered by it.

### Turn on the AI features (once)

The app runs without them, but search, link reading, research and letter writing need Gemini on Vertex AI in a Google Cloud project **with billing enabled**:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable aiplatform.googleapis.com
gcloud auth application-default login        # lets the app use your credentials locally
```

Then put `GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID` in `.env` and restart. Until it is set, AI actions return a clear "Set GOOGLE_CLOUD_PROJECT" message.

### Connect Gmail locally (optional)

1. `gcloud services enable gmail.googleapis.com`
2. Console → *Google Auth Platform*: configure the consent screen (External, add your own address as a **test user**) and add the scopes `gmail.readonly` and `gmail.compose`.
3. *Clients* → create an OAuth client of type **Desktop app** and download the JSON.
4. In the app open **Inbox & Gmail**, upload the JSON, press **Connect Gmail**, and approve in the browser tab that opens.

While the consent screen is in *testing* mode Google expires the token after 7 days, so either publish the app (fine for personal use) or reconnect weekly. To have the app check your mail on a timer while it runs, set `WATCH_INTERVAL_MIN=20` in `.env`. For an always-on setup see the deployment guide below.

**Just looking around?** Run the demo with sample data and no Google account:

```bash
cd backend && python demo.py     # http://127.0.0.1:8777
```

**Docker:** `docker compose up --build` serves the app on `127.0.0.1:8000`. AI features need Google credentials inside the container; running natively after `gcloud auth application-default login` is simpler.

### Chrome extension

1. `chrome://extensions` → enable *Developer mode* → **Load unpacked** → select `extension/`.
2. Open LinkedIn → *Saved posts*, click the extension, press **Scroll & send all saved posts**.
3. For a hosted server, open the popup's *Server settings* and enter the server URL and your `EXTENSION_TOKEN`.

## ☁️ Run it always-on

[`docs/DEPLOY.md`](docs/DEPLOY.md) walks through a single Google Cloud VM with Docker and Caddy (automatic HTTPS), a password login, daily snapshots and a background Gmail watcher, roughly €15/month. Gmail setup (OAuth consent, scopes, redirect URI) is covered there too.

## ⚙️ Configuration

All settings are environment variables (see [`.env.example`](.env.example)).

| Variable | Purpose |
|---|---|
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GEMINI_MODEL` | Vertex AI / Gemini |
| `APP_PASSWORD` | Turns on the login page (hosted mode); empty = open, for local use only |
| `SESSION_SECRET` | Signs the login cookie |
| `EXTENSION_TOKEN` | Lets the Chrome extension post LinkedIn data (two endpoints only) |
| `PUBLIC_URL`, `ALLOWED_HOSTS`, `DOMAIN` | Public address, accepted Host headers, Caddy site name |
| `WATCH_INTERVAL_MIN` | Minutes between background Gmail checks (`0` = off) |
| `SP_DATA_DIR` | Where the database and Gmail token live |

## 🧱 How it works

FastAPI + SQLite on the backend, a framework-free JavaScript frontend with generated SVG artwork, and an MV3 Chrome extension. AI agents are focused Gemini prompts, many of them grounded with Google Search and run in parallel. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the module map, data model and the key flows.

```
backend/      FastAPI app (main · pipeline · inbox · agents · ai · auth · db) and demo.py
frontend/     index.html · dash.js · extra.js · theme.css
extension/    Chrome extension (scroll, de-duplicate, send)
tests/        pytest suite
docs/         architecture, deployment guide, screenshots
```

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest
```

CI runs the tests, syntax-checks the JavaScript and builds the Docker image on every push.

## ⚠️ Good to know

- AI-extracted dates, requirements and eligibility can be wrong. **Always confirm on the official page before you apply.**
- No tool can guarantee a text is "100% human" or passes every AI detector. The writing tools work by using your real details and voice and by pointing at generic sentences.
- Cold emails are drafted for you to review; nothing is sent without your explicit confirmation.
- Reading LinkedIn is limited to your own saved items, in your own browser session, and depends on LinkedIn's page markup.
- This is a personal project, not affiliated with any scholarship provider, LinkedIn or Google. Security notes are in [`SECURITY.md`](SECURITY.md).

## 📄 License

[MIT](LICENSE) © Mohab Islam
