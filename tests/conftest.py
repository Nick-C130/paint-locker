import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="paintlocker-test-")
os.environ["PAINTLOCKER_DATA_DIR"] = _TMP
os.environ["PAINTLOCKER_BACKUPS"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app, startup  # noqa: E402

startup()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def session():
    with db.SessionLocal() as s:
        yield s
