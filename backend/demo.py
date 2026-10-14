"""Run ScholarPilot with sample data in a throw-away database (no Google account needed).

    python demo.py            # http://127.0.0.1:8777

Everything except the AI-powered actions (search, link reading, letter writing, Gmail) works, so you can
explore the dashboard, application workspace and tracking flow.
"""
import pathlib
import sys
import tempfile
from datetime import date, timedelta

import db

db.DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "demo.db"  # never touches your real data

import inbox  # noqa: E402
import main  # noqa: E402


def days(n):
    return (date.today() + timedelta(days=n)).isoformat()


def seed():
    db.init()
    main.put_profile(main.Profile(data={
        "full_name": "Alex Demo", "nationality": "Egypt", "target_level": "Master's", "target_field": "Artificial Intelligence",
        "education": "BSc Computer Science", "language_tests": "IELTS 7.5"}))
    rows = [
        ("DAAD EPOS Scholarship", "DAAD", "Germany", 9, ["CV", "Transcript", "Passport copy", "Motivation letter"]),
        ("Chevening Scholarship", "UK FCDO", "United Kingdom", 34, ["CV", "Two references", "IELTS certificate"]),
        ("Swedish Institute Scholarship", "Swedish Institute", "Sweden", 61, ["CV", "Transcript"]),
        ("Erasmus Mundus Joint Master", "European Union", "Multiple", 90, ["CV", "Passport copy"]),
    ]
    for name, provider, country, d, docs in rows:
        sid = main.save_scholarship({"name": name, "provider": provider, "country": country, "level": "Master",
                                     "deadline": days(d), "open_date": days(-20), "required_docs": docs}, "manual")
        with db.conn() as c:
            for doc in docs:
                inbox.add_todo(c, sid, doc, "document")
            inbox.add_todo(c, sid, "Create an account on the application portal", "step")
            c.execute("UPDATE scholarships SET pinned=1, steps_done='[\"shortlist\"]' WHERE id=?", (sid,))
    # extra, untracked scholarships so the list, insights and bulk actions have something to show
    more = [("ETH Zurich Excellence Scholarship (ESOP)", "ETH Zurich", "Switzerland", 47, "online", "Free"),
            ("KAUST Fellowship", "KAUST", "Saudi Arabia", 75, "online", "USD 60"),
            ("Eiffel Excellence Scholarship", "Campus France", "France", 88, "both", "Free"),
            ("Swiss Government Excellence Scholarship", "SERI", "Switzerland", 52, "online", "Free"),
            ("MEXT University Recommended", "MEXT", "Japan", 33, "offline", "Free"),
            ("Türkiye Burslari", "YTB", "Turkey", 120, "online", "Free")]
    for i, (name, provider, country, d, mode, fee) in enumerate(more):
        sid = main.save_scholarship({"name": name, "provider": provider, "country": country, "level": "Master",
                                     "deadline": days(d), "open_date": days(-10), "application_mode": mode,
                                     "application_fee": fee, "required_docs": ["CV", "Transcript"]}, "manual")
        with db.conn() as c:
            c.execute("UPDATE scholarships SET source='google-sweep', verdict='qualified', confidence=?, created=? WHERE id=?",
                      ("confirmed" if i % 2 == 0 else "official", days(-(i * 2 % 12)) + " 09:00:00", sid))


if __name__ == "__main__":
    import uvicorn
    seed()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
    uvicorn.run(main.app, host="127.0.0.1", port=port)
