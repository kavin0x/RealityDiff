from realitydiff import config


def test_load_env_reads_allowed_keys_without_overriding(tmp_path, monkeypatch):
    f = tmp_path / "env"
    f.write_text(
        "# comment\nXAI_API_KEY=from-file\nOPENROUTER_API_KEY=or-from-file\n"
        "OPENROUTER_MODEL=openai/gpt-4o-mini\nREALITYDIFF_DB='/tmp/x.sqlite'\nOTHER=nope\n"
    )
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setenv("REALITYDIFF_DB", "/already/set.sqlite")
    monkeypatch.delenv("OTHER", raising=False)
    config.load_env(f)
    import os

    assert os.environ["XAI_API_KEY"] == "from-file"
    assert os.environ["OPENROUTER_API_KEY"] == "or-from-file"
    assert os.environ["OPENROUTER_MODEL"] == "openai/gpt-4o-mini"
    assert os.environ["REALITYDIFF_DB"] == "/already/set.sqlite"
    assert "OTHER" not in os.environ


def test_load_env_missing_file_is_fine(tmp_path):
    config.load_env(tmp_path / "missing")


def test_config_file_respects_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("REALITYDIFF_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.config_file() == tmp_path / "realitydiff" / "env"


def test_default_db_checkout_vs_installed(tmp_path, monkeypatch):
    monkeypatch.delenv("REALITYDIFF_DB", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.chdir(tmp_path)
    assert config.default_db() == str(tmp_path / "share" / "realitydiff" / "realitydiff.sqlite")
    (tmp_path / "data").mkdir()
    assert config.default_db() != "data/realitydiff.sqlite"  # a stray data/ dir is not enough
    (tmp_path / "data" / "realitydiff.sqlite").write_bytes(b"")
    assert config.default_db() == "data/realitydiff.sqlite"
    (tmp_path / "data" / "realitydiff.sqlite").unlink()
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "src" / "realitydiff").mkdir(parents=True)
    assert config.default_db() == "data/realitydiff.sqlite"
    monkeypatch.setenv("REALITYDIFF_DB", "/x.sqlite")
    assert config.default_db() == "/x.sqlite"


def test_load_env_last_value_wins_and_export_prefix(tmp_path, monkeypatch):
    import os

    f = tmp_path / "env"
    f.write_text("export XAI_API_KEY=old\nXAI_API_KEY=new\n")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    config.load_env(f)
    assert os.environ["XAI_API_KEY"] == "new"
