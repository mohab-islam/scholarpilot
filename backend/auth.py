"""Single-user login for the hosted app. Disabled (open) when APP_PASSWORD is not set, i.e. local use."""
import hashlib
import hmac
import os
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

PASSWORD = os.getenv("APP_PASSWORD", "")
SECRET = (os.getenv("SESSION_SECRET") or PASSWORD).encode()
EXT_TOKEN = os.getenv("EXTENSION_TOKEN", "")
ENABLED = bool(PASSWORD)
COOKIE = "sp_session"
MAX_AGE = 30 * 86400
SECURE = os.getenv("PUBLIC_URL", "").startswith("https")
EXT_PATHS = ("/api/linkedin/ingest", "/api/profile/import")  # the only endpoints the Chrome extension may call
PUBLIC_PATHS = ("/login", "/api/auth/login", "/static/theme.css")

router = APIRouter(prefix="/api/auth")
_fails = []


def _sig(ts):
    return hmac.new(SECRET, str(ts).encode(), hashlib.sha256).hexdigest()


def make_token():
    ts = int(time.time())
    return f"{ts}.{_sig(ts)}"


def valid_token(tok):
    try:
        ts, sig = tok.split(".")
        return hmac.compare_digest(sig, _sig(ts)) and time.time() - int(ts) < MAX_AGE
    except (ValueError, AttributeError):
        return False


async def middleware(request: Request, call_next):
    if not ENABLED:
        return await call_next(request)
    path = request.url.path
    if path in PUBLIC_PATHS or request.method == "OPTIONS":  # preflights carry no credentials; CORS rules still apply
        return await call_next(request)
    if valid_token(request.cookies.get(COOKIE, "")):
        return await call_next(request)
    key = request.headers.get("x-api-key", "")
    if EXT_TOKEN and path in EXT_PATHS and hmac.compare_digest(key, EXT_TOKEN):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Not signed in"}, status_code=401)
    return RedirectResponse("/login")


class LoginIn(BaseModel):
    password: str


@router.post("/login")
def login(b: LoginIn):
    now = time.time()
    _fails[:] = [t for t in _fails if now - t < 300]
    if len(_fails) >= 5:
        raise HTTPException(429, "Too many attempts. Wait a few minutes.")
    if not hmac.compare_digest(b.password.encode(), PASSWORD.encode()):
        _fails.append(now)
        raise HTTPException(401, "Wrong password")
    r = JSONResponse({"ok": True})
    r.set_cookie(COOKIE, make_token(), max_age=MAX_AGE, httponly=True, secure=SECURE, samesite="lax")
    return r


@router.post("/logout")
def logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE)
    return r


LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ScholarPilot</title><link rel="stylesheet" href="/static/theme.css">
<style>body{display:grid;place-items:center;min-height:100vh}.box{width:min(380px,92vw);padding:34px;border-radius:32px;background:var(--glass);
backdrop-filter:blur(40px);border:1px solid var(--edge);box-shadow:var(--shadow)}h1{margin:0 0 4px;font-size:26px;letter-spacing:-.03em}button{width:100%;justify-content:center;margin-top:16px}
#e{color:var(--bad);font-size:13px;min-height:18px;margin-top:8px}</style></head><body>
<div class="ambient"><i></i><i></i><i></i></div>
<form class="box" id="f"><h1>ScholarPilot</h1><div class="mute">Private workspace. Sign in to continue.</div>
<label>Password</label><input type="password" id="p" autofocus autocomplete="current-password"><div id="e"></div>
<button class="btn">Sign in</button></form>
<script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.getElementById('p').value})});
if(r.ok)location.href='/';else document.getElementById('e').textContent=(await r.json()).detail||'Error'}</script></body></html>"""


def login_page():
    return HTMLResponse(LOGIN_HTML)
