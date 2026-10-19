import os
import tempfile
import uuid

os.environ.setdefault("DATABASE_URL_APP", "postgresql+psycopg://genranq_app:genranq_app@localhost:5432/genranq_test")
os.environ.setdefault("DATABASE_URL_SYSTEM", "postgresql+psycopg://genranq_owner:genranq_owner@localhost:5432/genranq_test")
os.environ["ALLOW_PRIVATE_CRAWL"] = "1"
os.environ["CRAWL_DELAY_SEC"] = "0"
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="genranq-test-")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import migrate  # noqa: E402
from app.main import app  # noqa: E402
from tests.fixtures import serve  # noqa: E402

FIXTURE_PORT = int(os.environ.get("FIXTURE_PORT", "8799"))


@pytest.fixture(scope="session", autouse=True)
def _db():
    migrate.run()
    yield


@pytest.fixture(scope="session")
def site_server():
    srv = serve.start(FIXTURE_PORT)
    yield f"http://127.0.0.1:{FIXTURE_PORT}"
    srv.shutdown()


@pytest.fixture
def client():
    return TestClient(app)


class Org:
    def __init__(self, client, plan="advanced", email=None):
        self.c = client
        self.email = email or f"owner-{uuid.uuid4().hex[:8]}@acmeagency.io"
        self.password = "correct-horse-battery"
        r = client.post("/api/auth/signup", json={"organisation": f"Org {uuid.uuid4().hex[:6]}", "name": "Owner",
                                                  "email": self.email, "password": self.password, "plan": plan})
        assert r.status_code == 200, r.text
        self.tokens = r.json()
        self.org_id = r.json()["organisation"]["id"]

    @property
    def h(self):
        return {"Authorization": f"Bearer {self.tokens['access_token']}"}

    def get(self, path, **kw):
        return self.c.get("/api" + path, headers=self.h, **kw)

    def post(self, path, json=None, **kw):
        return self.c.post("/api" + path, json=json, headers=kw.pop("headers", self.h), **kw)

    def patch(self, path, json=None):
        return self.c.patch("/api" + path, json=json, headers=self.h)

    def invite_user(self, role):
        r = self.post("/team/invites", {"email": f"{role}-{uuid.uuid4().hex[:6]}@acmeagency.io", "role": role})
        assert r.status_code == 200, r.text
        tok = r.json()["accept_token"]
        r = self.c.post("/api/auth/invites/accept", json={"token": tok, "name": role, "password": "another-good-pass"})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def project(self, name="P"):
        r = self.post("/projects", {"name": name})
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def org(client):
    return Org(client)


@pytest.fixture
def org_factory(client):
    return lambda **kw: Org(client, **kw)
