# Security

ScholarPilot is a **single-user** application that stores personal documents and can read and draft email on your behalf. Treat a deployment like a password manager.

## Model

- **No secrets in the repo.** Configuration comes from environment variables (`.env`, git-ignored). The Gmail OAuth client file and token live in the data directory, outside version control.
- **Hosted mode** (`APP_PASSWORD` set): every route requires a signed, HttpOnly, SameSite=Lax session cookie. Failed logins are rate-limited. The Chrome extension uses a separate token that is accepted on only two endpoints (`/api/linkedin/ingest`, `/api/profile/import`).
- **Host and origin checks:** only `localhost` and the hosts in `ALLOWED_HOSTS` are served; cross-origin requests are allowed only from `chrome-extension://` origins. This blocks other websites and DNS-rebinding from driving the API.
- **Email:** the app only ever sends a message when you press *Send* and confirm. Drafts are created otherwise. Only emails that mention a scholarship or professor you track are sent to the model for classification.
- **AI:** prompts and uploaded document text are sent to Gemini on Vertex AI in your own Google Cloud project.

## Known limits

- One shared password, no per-user accounts or audit log.
- Anyone with owner access to your cloud project can read the VM disk.
- LinkedIn page scraping depends on LinkedIn's markup and is for your own saved items only.

## Reporting a vulnerability

Please open a private security advisory on GitHub (Security → Report a vulnerability) rather than a public issue.
