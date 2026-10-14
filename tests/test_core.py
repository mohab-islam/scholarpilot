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
    # automatically found scholarships wait for verification; the default list shows only verified ones
    assert client.get("/api/scholarships").json() == []
    assert [s["name"] for s in client.get("/api/scholarships", params={"view": "pending"}).json()] == ["Good"]


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
    assert [m["text"] for m in dash["missing"]] == ["Passport / ID copy"]  # the CV is covered by the upload

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


def test_profile_picture_roundtrip(client):
    assert client.get("/api/avatar").status_code == 404
    r = client.post("/api/avatar", files={"file": ("me.png", b"\x89PNG fake", "image/png")})
    assert r.status_code == 200
    got = client.get("/api/avatar")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert client.post("/api/avatar", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400


def test_verification_decisions(client):
    import verifier
    want = {"master"}
    assert verifier.decide({"deadline": day(30), "level": "Master", "fit": "likely"}, want)[0] == "qualified"
    assert verifier.decide({"deadline": day(-2), "level": "Master"}, want)[0] == "rejected"
    assert verifier.decide({"deadline": day(30), "level": "Master", "fit": "no", "fit_reason": "EU citizens only"}, want)[0] == "rejected"
    assert verifier.decide({"deadline": day(30), "level": "PhD"}, want)[0] == "rejected"
    assert verifier.decide({"open_date": day(40)}, want)[0] == "qualified"      # opens in the future
    assert verifier.decide({}, want)[0] == "review"                              # no dates anywhere


def test_manual_and_tracked_scholarships_are_always_listed(client):
    sid = client.post("/api/scholarships", json={"name": "Mine", "deadline": day(-30)}).json()["id"]
    assert [s["name"] for s in client.get("/api/scholarships").json()] == ["Mine"]
    assert client.get("/api/scholarships/counts").json()["qualified"] == 1


def test_linkedin_ingest_is_queued_and_triaged_without_ai(client):
    import jobs
    client.put("/api/profile", json={"data": PROFILE})
    posts = [
        {"url": "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000011/", "text": "Fully funded DAAD scholarship for masters students, apply now " * 2},
        {"url": "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000011/?x=1", "text": "duplicate of the first"},
        {"url": "", "text": "We are hiring a backend engineer, great benefits and remote work, apply on our site " * 2},
    ]
    r = client.post("/api/linkedin/ingest", json={"posts": posts}).json()
    assert r["queued"] == 1 and r["not_scholarship"] == 1 and r["already_seen"] + r["duplicates"] == 1
    assert jobs.queue_counts().get("pending") == 1
    again = client.post("/api/linkedin/ingest", json={"posts": posts}).json()
    assert again["queued"] == 0 and again["already_seen"] == 3


def test_notifications_and_settings(client):
    import db
    with db.conn() as c:
        c.execute("INSERT INTO notifications(kind,title,body,ref_id) VALUES('new_scholarship','New: X','d',1)")
    got = client.get("/api/notifications").json()
    assert got["unread"] == 1
    client.post("/api/notifications/read")
    assert client.get("/api/notifications").json()["unread"] == 0
    assert client.put("/api/settings", json={"sweep_hour_utc": 7, "notify_email": False}).json()["sweep_hour_utc"] == 7
    assert client.get("/api/settings").json()["notify_email"] is False


def test_overlapping_records_are_recognised_and_merged(client):
    import db
    import main
    a = {"name": "Gates Cambridge Scholarship", "provider": "University of Cambridge", "url": "https://www.gatescambridge.org/apply/"}
    b = {"name": "Gates Cambridge", "provider": "", "url": "https://gatescambridge.org/apply?utm=x"}
    c = {"name": "Chevening Scholarship", "provider": "UK FCDO", "url": ""}
    assert main.same_scholarship(a, b) and not main.same_scholarship(a, c)
    with db.conn() as con:
        for r in (a, b, c):
            con.execute("INSERT INTO scholarships(name,provider,url,source,verdict) VALUES(?,?,?,?,?)",
                        (r["name"], r["provider"], r["url"] or None, "google", "qualified"))
    assert main.merge_duplicates() == 1
    names = sorted(s["name"] for s in client.get("/api/scholarships", params={"view": "all"}).json())
    assert len(names) == 2


def test_double_check_prefers_the_official_page_and_linkedin_must_be_open():
    import verifier
    assert verifier.reconcile(day(30), day(30))[1] == "confirmed"
    use, conf, note = verifier.reconcile(day(20), day(35))
    assert use == day(35) and conf == "official" and "corrected" in note
    assert verifier.reconcile(day(20), "")[1] == "single-source"
    want = {"master"}
    upcoming = {"source": "linkedin", "level": "Master", "open_date": day(40), "deadline": day(90)}
    assert verifier.decide(upcoming, want)[0] == "rejected"
    assert verifier.decide({**upcoming, "source": "google-sweep"}, want)[0] == "qualified"
    assert verifier.decide({**upcoming, "open_date": day(-5)}, want)[0] == "qualified"


def test_same_document_is_grouped_across_scholarships(client):
    import db
    import inbox
    ids = []
    for name in ("Alpha Grant", "Beta Fellowship"):
        sid = client.post("/api/scholarships", json={"name": name, "deadline": day(30)}).json()["id"]
        client.post(f"/api/scholarships/{sid}/pin", json={"pinned": True})
        ids.append(sid)
    with db.conn() as c:
        inbox.add_todo(c, ids[0], "Curriculum Vitae", "document")
        inbox.add_todo(c, ids[1], "CV (max 2 pages)", "document")
        inbox.add_todo(c, ids[1], "Transcript of records", "document")
    missing = {m["text"]: m for m in client.get("/api/dashboard").json()["missing"]}
    assert missing["CV / Resume"]["count"] == 2
    assert missing["Academic transcripts"]["count"] == 1


def test_bulk_track_and_skip(client):
    a = client.post("/api/scholarships", json={"name": "One"}).json()["id"]
    b = client.post("/api/scholarships", json={"name": "Two"}).json()["id"]
    assert client.post("/api/scholarships/bulk", json={"ids": [a, b], "action": "track"}).json()["changed"] == 2
    assert client.get("/api/scholarships/counts").json()["qualified"] == 2
    client.post("/api/scholarships/bulk", json={"ids": [a], "action": "untrack"})
    client.post("/api/scholarships/bulk", json={"ids": [a], "action": "skip"})
    shown = [s["name"] for s in client.get("/api/scholarships").json()]
    assert shown == ["Two"]


def test_stats_endpoint_shape(client):
    s = client.get("/api/stats").json()
    assert len(s["per_day"]) == 14 and set(s["funnel"]) >= {"found", "qualified", "tracked"}
