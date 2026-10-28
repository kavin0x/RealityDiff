import pytest
from fastapi.testclient import TestClient

from realitydiff import cli
from realitydiff.api import create_app
from realitydiff.security import bind_check, host_from_header
from realitydiff.service import RealityDiff
from realitydiff.store import BeliefStore
from tests.test_reasoner import INIT, ScriptedModel

TOKEN = "test-token-not-a-secret-123"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("REALITYDIFF_CONFIG", str(tmp_path / "cfg" / "env"))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return tmp_path


def _engine(tmp_path):
    return RealityDiff(BeliefStore(tmp_path / "db.sqlite"), model=ScriptedModel([INIT]))


def _client(tmp_path, base="http://127.0.0.1:8000", **kw):
    return TestClient(create_app(_engine(tmp_path), start_watcher=False, **kw), base_url=base)


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/claims"),
        ("get", "/api/compare/history"),
        ("get", "/api/providers"),
        ("post", "/api/compare"),
        ("post", "/api/claims"),
        ("get", "/"),
        ("get", "/static/app.js"),
    ],
)
def test_foreign_host_rejected_everywhere(tmp_path, method, path):
    with _client(tmp_path) as client:
        kw = {"json": {"question": "x?", "models": []}} if method == "post" else {}
        res = getattr(client, method)(path, headers={"Host": "evil.example:8000"}, **kw)
        assert res.status_code == 400
        assert res.json()["detail"] == "Invalid host header"


@pytest.mark.parametrize(
    "host", ["127.0.0.1:8000", "localhost:8000", "[::1]:8000", "127.0.0.1", "localhost", "LOCALHOST:9", "[::1]"]
)
def test_loopback_hosts_accepted(tmp_path, host):
    with _client(tmp_path) as client:
        assert client.get("/api/health", headers={"Host": host}).status_code == 200
        assert client.get("/api/claims", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize(
    "host", ["evil.example", "127.0.0.1.evil.example:8000", "localhost.evil:80", "0.0.0.0:8000", "", "[::1]x", "a:b:c"]
)
def test_tricky_hosts_rejected(tmp_path, host):
    with _client(tmp_path) as client:
        assert client.get("/api/health", headers={"Host": host}).status_code == 400


def test_foreign_origin_post_rejected(tmp_path):
    with _client(tmp_path) as client:
        for origin in ("http://evil.example", "null", "http://127.0.0.1.evil.example:8000"):
            res = client.post(
                "/api/claims",
                json={"statement": "Apple is going to replace Siri."},
                headers={"Origin": origin},
            )
            assert res.status_code == 403, origin
        res = client.delete("/api/service", headers={"Origin": "https://evil.example"})
        assert res.status_code == 403


def test_no_origin_post_allowed(tmp_path):
    with _client(tmp_path) as client:
        res = client.post("/api/claims", json={"statement": "Apple is going to replace Siri."})
        assert res.status_code == 200


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"])
def test_loopback_origin_post_allowed(tmp_path, origin):
    with _client(tmp_path) as client:
        res = client.post(
            "/api/claims",
            json={"statement": "Apple is going to replace Siri."},
            headers={"Origin": origin},
        )
        assert res.status_code == 200


def test_foreign_origin_get_still_needs_loopback_host(tmp_path):
    # A plain GET with a foreign Origin is fine (no CORS headers, so the page can't read it).
    with _client(tmp_path) as client:
        assert client.get("/api/health", headers={"Origin": "http://evil.example"}).status_code == 200


def test_loopback_bind_ignores_token(tmp_path, monkeypatch):
    monkeypatch.setenv("REALITYDIFF_TOKEN", TOKEN)
    with _client(tmp_path) as client:
        assert client.get("/api/claims").status_code == 200


def test_non_loopback_app_refuses_without_token(tmp_path):
    with pytest.raises(RuntimeError, match="REALITYDIFF_TOKEN"):
        create_app(_engine(tmp_path), start_watcher=False, bind_host="0.0.0.0")


def test_token_required_and_checked_on_api(tmp_path, monkeypatch):
    monkeypatch.setenv("REALITYDIFF_TOKEN", TOKEN)
    monkeypatch.setenv("REALITYDIFF_ALLOWED_HOSTS", "box.lan")
    with _client(tmp_path, base="http://box.lan:8000", bind_host="0.0.0.0") as client:
        assert client.get("/api/claims").status_code == 401
        assert client.get("/api/claims", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/api/claims", headers={"X-RealityDiff-Token": "wrong"}).status_code == 401
        assert client.get("/api/claims", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
        assert client.get("/api/claims", headers={"X-RealityDiff-Token": TOKEN}).status_code == 200
        # the page itself loads without a token; the API calls ask for it
        assert client.get("/").status_code == 200
        # hosts that were not configured still fail, token or not
        res = client.get(
            "/api/claims", headers={"Host": "evil.example", "Authorization": f"Bearer {TOKEN}"}
        )
        assert res.status_code == 400
        # same-origin browser writes work, foreign ones do not
        ok = client.post(
            "/api/claims",
            json={"statement": "Apple is going to replace Siri."},
            headers={"Origin": "http://box.lan:8000", "X-RealityDiff-Token": TOKEN},
        )
        assert ok.status_code == 200
        bad = client.post(
            "/api/claims",
            json={"statement": "Apple is going to replace Siri."},
            headers={"Origin": "http://evil.example", "X-RealityDiff-Token": TOKEN},
        )
        assert bad.status_code == 403


def test_bind_host_itself_is_allowed(tmp_path):
    with _client(tmp_path, base="http://192.168.1.5:8000", bind_host="192.168.1.5", token=TOKEN) as client:
        assert client.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_bind_check():
    assert bind_check("127.0.0.1", "") is None
    assert bind_check("::1", "") is None
    assert bind_check("0.0.0.0", "x") is None
    assert "REALITYDIFF_TOKEN" in bind_check("0.0.0.0", "")


def test_host_parsing():
    assert host_from_header("[::1]:8000") == "::1"
    assert host_from_header("LocalHost:1") == "localhost"
    assert host_from_header("::1") is None
    assert host_from_header("x:") is None


def _fake_uvicorn(monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, **kw: seen.update(host=host))
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    return seen


def test_serve_refuses_non_loopback_without_token(monkeypatch, capsys):
    seen = _fake_uvicorn(monkeypatch)
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 2
    assert not seen
    assert "REALITYDIFF_TOKEN" in capsys.readouterr().err


def test_serve_allows_non_loopback_with_token_and_never_prints_it(monkeypatch, capsys):
    seen = _fake_uvicorn(monkeypatch)
    monkeypatch.setenv("REALITYDIFF_TOKEN", TOKEN)
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 0
    assert seen["host"] == "0.0.0.0"
    out = capsys.readouterr()
    assert TOKEN not in out.err + out.out
    assert "REALITYDIFF_ALLOWED_HOSTS" in out.err


def test_serve_reads_token_from_config_file(isolated, monkeypatch):
    seen = _fake_uvicorn(monkeypatch)
    cfg = isolated / "cfg" / "env"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f"REALITYDIFF_TOKEN={TOKEN}\n")
    from realitydiff.config import load_env

    load_env()
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 0
    assert seen["host"] == "0.0.0.0"


def test_default_serve_needs_no_token(monkeypatch):
    seen = _fake_uvicorn(monkeypatch)
    assert cli.main(["serve"]) == 0
    assert seen["host"] == "127.0.0.1"
