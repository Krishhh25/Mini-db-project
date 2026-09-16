import os
import sys
import importlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh Flask app + fresh MiniDB file per test, with auth disabled."""
    db_file = str(tmp_path / "web_test.db")
    monkeypatch.setenv("MINIDB_FILE", db_file)
    monkeypatch.delenv("MINIDB_AUTH_TOKEN", raising=False)

    import web.app as web_app
    importlib.reload(web_app)  # pick up the new MINIDB_FILE env var

    with web_app.app.test_client() as c:
        yield c


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    db_file = str(tmp_path / "web_auth_test.db")
    monkeypatch.setenv("MINIDB_FILE", db_file)
    monkeypatch.setenv("MINIDB_AUTH_TOKEN", "topsecret")

    import web.app as web_app
    importlib.reload(web_app)

    with web_app.app.test_client() as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_homepage_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.content_type
    assert b"MiniDB" in r.data


def test_set_and_get_via_api(client):
    r = client.post("/api/command", json={"command": "SET user1 name=Alice age=30"})
    assert r.status_code == 200
    assert "Set user1" in r.get_json()["result"]

    r = client.post("/api/command", json={"command": "GET user1"})
    assert r.status_code == 200
    assert "Alice" in r.get_json()["result"]


def test_find_with_index(client):
    client.post("/api/command", json={"command": "SET user1 age=30"})
    client.post("/api/command", json={"command": "SET user2 age=17"})
    client.post("/api/command", json={"command": "INDEX age"})

    r = client.post("/api/command", json={"command": "FIND age>=18"})
    result = r.get_json()["result"]
    assert "user1" in result
    assert "user2" not in result


def test_missing_command_returns_400(client):
    r = client.post("/api/command", json={})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_stateful_commands_are_blocked(client):
    for cmd in ("BEGIN", "COMMIT", "ROLLBACK"):
        r = client.post("/api/command", json={"command": cmd})
        assert r.status_code == 200
        assert "not supported" in r.get_json()["result"] or "aren't supported" in r.get_json()["result"]


def test_auth_required_when_token_set(auth_client):
    r = auth_client.post("/api/command", json={"command": "SET u1 a=1"})
    assert r.status_code == 401

    r = auth_client.post("/api/command", json={"command": "SET u1 a=1"},
                          headers={"X-API-Token": "wrong"})
    assert r.status_code == 401

    r = auth_client.post("/api/command", json={"command": "SET u1 a=1"},
                          headers={"X-API-Token": "topsecret"})
    assert r.status_code == 200
