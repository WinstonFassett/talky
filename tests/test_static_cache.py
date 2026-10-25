"""SWE-618: index.html must revalidate (Talky.app WKWebView served a stale UI)."""
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient


def test_static_cache_headers(tmp_path, monkeypatch):
    from talky.server import __main__ as m

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "assets" / "a-abc123.js").write_text("x")
    client = TestClient(Starlette(routes=[Mount("/", app=m._RevalidatingStaticFiles(directory=str(tmp_path), html=True))]))
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert "cache-control" not in client.get("/assets/a-abc123.js").headers


def test_daemon_build_has_sha():
    from talky.server import __main__ as m
    assert set(m._daemon_build()) == {"version", "sha"}
