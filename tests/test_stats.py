import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def fake_db(tmp_path, monkeypatch):
    db_file = tmp_path / "marginalia.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE documents (id INTEGER PRIMARY KEY, title TEXT, content TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, doc_id INTEGER, text TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, chunk_id INTEGER, vector BLOB);
        INSERT INTO documents VALUES (1, 'Doc A', 'hello world');
        INSERT INTO documents VALUES (2, 'Doc B', 'foo bar');
        INSERT INTO chunks VALUES (1,1,'hello'),(2,1,'world'),(3,2,'foo bar');
        INSERT INTO embeddings VALUES (1,1,NULL),(2,2,NULL),(3,3,NULL);
    """)
    conn.commit(); conn.close()
    monkeypatch.setenv("MARGINALIA_DB_PATH", str(db_file))
    return db_file


@pytest.fixture()
def client(fake_db):
    from routers.stats import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_stats_200(client):
    assert client.get("/api/stats").status_code == 200

def test_doc_count(client):
    assert client.get("/api/stats").json()["documents"] == 2

def test_chunk_count(client):
    assert client.get("/api/stats").json()["chunks"] == 3

def test_embedding_count(client):
    assert client.get("/api/stats").json()["embeddings"] == 3

def test_db_size_positive(client):
    assert client.get("/api/stats").json()["db_size_bytes"] > 0

def test_missing_db_503(monkeypatch):
    monkeypatch.setenv("MARGINALIA_DB_PATH", "/nonexistent/path.db")
    import importlib, routers.stats as m
    importlib.reload(m)
    app = FastAPI(); app.include_router(m.router)
    r = TestClient(app, raise_server_exceptions=False).get("/api/stats")
    assert r.status_code == 503
