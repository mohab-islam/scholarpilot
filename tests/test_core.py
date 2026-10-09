import json
import time
from datetime import date, timedelta

import pytest

PROFILE = {"nationality": "Egypt", "target_level": "Master's", "target_field": "AI"}


def day(n):
    return (date.today() + timedelta(days=n)).isoformat()


# ---------- pure helpers ----------
def test_scholarship_names_are_normalised_for_deduplication():
    import main
    assert main.norm_name("DAAD EPOS Scholarship 2027") == main.norm_name("DAAD EPOS Scholarships 2026/27")
    assert main.norm_name("Chevening Scholarship") != main.norm_name("Fulbright Scholarship")


def test_post_keys_identify_the_same_linkedin_post():
    import main
    a = main.Post(url="https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001/", text="DAAD scholarship open")
    b = main.Post(url="https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001/?utm=x", text="something else")
    assert set(main.post_keys(a)) & set(main.post_keys(b))  # shared activity id


def test_keyword_prefilter_separates_scholarships_from_noise():
    import main
    assert main.STRONG.search("Fully funded Chevening opportunity")
    assert not main.STRONG.search("We're hiring a senior Python developer, apply now")


def test_phase_of_dates():
    import main
    assert main.phase_of({"deadline": day(-1)}) == "closed"
    assert main.phase_of({"deadline": day(30), "open_date": day(10)}) == "upcoming"
    assert main.phase_of({"deadline": day(30), "open_date": day(-5)}) == "open"
    assert main.phase_of({}) == "unknown"


def test_signed_session_tokens(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "SECRET", b"test-secret")
    tok = auth.make_token()
    assert auth.valid_token(tok)
    assert not auth.valid_token(tok[:-1] + ("0" if tok[-1] != "0" else "1"))
    assert not auth.valid_token("garbage")
    old = f"{int(time.time()) - auth.MAX_AGE - 5}"
    assert not auth.valid_token(f"{old}.{auth._sig(old)}")


# ---------- API behaviour ----------
def test_search_requires_a_profile_first(client):
    r = client.post("/api/discover", json={})
    assert r.status_code == 400 and "profile" in r.json()["detail"].lower()
    client.put("/api/profile", json={"data": PROFILE})
    assert client.get("/api/profile/status").json()["core_complete"] is True


def test_discovered_scholarships_are_filtered_by_eligibility_and_dates(client):
    import main
    client.put("/api/profile", json={"data": PROFILE})
    assert main.save_scholarship({"name": "Closed one", "level": "Master", "deadline": day(-3)}, "google") is None
    assert main.save_scholarship({"name": "Wrong level", "level": "PhD", "deadline": day(30)}, "google") is None
    assert main.save_scholarship({"name": "Not for me", "level": "Master", "deadline": day(30), "fit": "no"}, "google") is None
    assert main.save_scholarship({"name": "Good", "level": "Master", "deadline": day(30), "fit": "likely"}, "google")
    names = [s["name"] for s in client.get("/api/scholarships").json()]
    assert names == ["Good"]


def test_tracking_builds_todos_and_the_missing_papers_list(client):
    import db
    import inbox
    sid = client.post("/api/scholarships", json={"name": "Demo", "deadline": day(20)}).json()["id"]
    with db.conn() as c:
        inbox.add_todo(c, sid, "CV", "document")
        inbox.add_todo(c, sid, "Passport copy", "document")
    client.post(f"/api/scholarships/{sid}/pin", json={"pinned": True})
    client.post("/api/documents?kind=cv", files={"file": ("cv.txt", b"my cv " * 20)})

    dash = client.get("/api/dashboard").json()
    assert dash["next"]["name"] == "Demo"
    assert [m["text"] for m in dash["missing"]] == ["Passport copy"]  # the CV is covered by the upload

    tid = next(t["id"] for t in client.get(f"/api/scholarships/{sid}/todos").json() if t["text"] == "Passport copy")
    client.patch(f"/api/todos/{tid}", json={"done": True})
    assert client.get("/api/dashboard").json()["missing"] == []


def test_letter_versions_diff_and_restore(client):
    import main
    letter = main.save_letter(None, "motivation", "Test", "I love tapestry of ideas. Short one.")
    lid = letter["id"]
    client.put(f"/api/letters/{lid}", json={"content": "I like building things. Short one."})
    versions = client.get(f"/api/letters/{lid}/versions").json()
    assert [v["n"] for v in versions] == [2, 1]
    ops = client.get(f"/api/letters/{lid}/diff", params={"a": 1, "b": 2}).json()
    assert {o["op"] for o in ops} == {"eq", "del", "ins"}
    assert client.post(f"/api/letters/{lid}/restore/1").json()["content"].startswith("I love tapestry")
    assert client.get(f"/api/letters/{lid}/versions").json()[0]["n"] == 3


def test_authenticity_scan_flags_stock_phrases():
    import ai
    scan = ai.heuristic_ai_scan("I am excited to leverage the tapestry of ideas. Short.")
    assert "tapestry" in scan["stock_phrases"] and "leverage" in scan["stock_phrases"]


def test_only_local_hosts_are_served(client):
    from fastapi.testclient import TestClient
    import main
    assert TestClient(main.app, base_url="http://evil.example").get("/").status_code == 400


def test_foreign_origins_get_no_cors_access(client):
    r = client.options("/api/profile", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "PUT"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
