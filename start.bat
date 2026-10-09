@echo off
cd /d %~dp0
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -q -r backend\requirements.txt
cd backend
echo Open http://127.0.0.1:8000
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
