import os
import sys
import tempfile
from pathlib import Path

# Isolated data dir and no hosted-mode login, set before the app is imported.
os.environ["SP_DATA_DIR"] = tempfile.mkdtemp(prefix="scholarpilot-test-")
for var in ("APP_PASSWORD", "PUBLIC_URL", "EXTENSION_TOKEN", "WATCH_INTERVAL_MIN"):
    os.environ.pop(var, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import pytest  # noqa: E402


@pytest.fixture()
def client():
    import db
    import main
    from fastapi.testclient import TestClient

    with db.conn() as c:  # fresh tables for every test
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            c.execute(f"DELETE FROM {t}")
    return TestClient(main.app, base_url="http://127.0.0.1")
