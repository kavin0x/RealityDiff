import pytest


@pytest.fixture(autouse=True)
def _no_server_guard_env(monkeypatch):
    # cli serve writes REALITYDIFF_BIND_HOST, so reset these around every test.
    for name in ("REALITYDIFF_BIND_HOST", "REALITYDIFF_TOKEN", "REALITYDIFF_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
