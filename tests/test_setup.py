import io
import os
import stat

import pytest

from realitydiff import cli, config, setup


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("REALITYDIFF_CONFIG", str(tmp_path / "cfg" / "env"))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    return tmp_path


def run(**kw):
    out = io.StringIO()
    code = setup.run_setup(out=out, **kw)
    return code, out.getvalue()


def test_saves_key_with_600_and_never_prints_it(isolated):
    code, text = run(
        interactive=True,
        ask=lambda _p: "y",
        ask_secret=lambda _p: "xai-abc_123.DEF",
    )
    assert code == 0
    cfg = isolated / "cfg" / "env"
    assert "XAI_API_KEY=xai-abc_123.DEF" in cfg.read_text()
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert "xai-abc_123" not in text
    assert "realitydiff serve" in text
    assert config.setup_marker().exists()


def test_declining_key_still_finishes_and_marks_done(isolated):
    code, text = run(interactive=True, ask=lambda _p: "n", ask_secret=lambda _p: pytest.fail("asked"))
    assert code == 0
    assert not (isolated / "cfg" / "env").exists()
    assert "skipped" in text
    assert config.setup_marker().exists()


def test_yes_mode_never_prompts(isolated):
    code, text = run(assume_yes=True, ask=lambda _p: pytest.fail("prompted"), ask_secret=lambda _p: pytest.fail("prompted"))
    assert code == 0
    assert "non-interactive" in text


def test_no_key_flag_skips(isolated):
    _, text = run(interactive=True, skip_key=True, ask=lambda _p: pytest.fail("prompted"))
    assert "skipped (--no-key)" in text


def test_rejects_key_with_bad_characters(isolated):
    _, text = run(interactive=True, ask=lambda _p: "y", ask_secret=lambda _p: 'bad key"')
    assert "not saved" in text
    assert not (isolated / "cfg" / "env").exists()


def test_set_config_keeps_other_lines(isolated):
    path = isolated / "cfg" / "env"
    config.set_config("REALITYDIFF_DB", "/x.sqlite", path)
    config.set_config("XAI_API_KEY", "one", path)
    config.set_config("XAI_API_KEY", "two", path)
    lines = path.read_text().splitlines()
    assert "REALITYDIFF_DB=/x.sqlite" in lines
    assert lines.count("XAI_API_KEY=two") == 1
    assert "XAI_API_KEY=one" not in lines


def test_needs_first_run_only_on_a_fresh_terminal(isolated, monkeypatch):
    monkeypatch.setattr(setup, "is_interactive", lambda: True)
    assert setup.needs_first_run()
    config.set_config("XAI_API_KEY", "k", isolated / "cfg" / "env")
    assert not setup.needs_first_run()
    monkeypatch.setattr(setup, "is_interactive", lambda: False)
    (isolated / "cfg" / "env").unlink()
    assert not setup.needs_first_run()


def test_serve_without_key_prints_a_clean_error_not_a_traceback(isolated, monkeypatch, capsys):
    monkeypatch.setattr(setup, "is_interactive", lambda: False)
    assert cli.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "XAI_API_KEY is not set" in err
    assert "realitydiff setup" in err
    assert "Traceback" not in err


def test_bare_command_runs_setup_first_time_then_help(isolated, monkeypatch, capsys):
    monkeypatch.setattr(cli, "needs_first_run", lambda: True)
    called = []
    monkeypatch.setattr(cli, "run_setup", lambda **kw: called.append(1) or 0)
    assert cli.main([]) == 0 and called
    monkeypatch.setattr(cli, "needs_first_run", lambda: False)
    assert cli.main([]) == 0
    assert "usage: realitydiff" in capsys.readouterr().out


def test_serve_binds_to_localhost_by_default():
    import argparse

    seen = {}
    import uvicorn

    parser_calls = {}

    def fake_run(app, host, port, **kw):
        parser_calls["host"] = host

    import pytest as _p

    with _p.MonkeyPatch.context() as mp:
        mp.setattr(uvicorn, "run", fake_run)
        mp.setenv("XAI_API_KEY", "k")
        cli.main(["serve"])
    assert parser_calls["host"] == "127.0.0.1"


def test_set_config_leaves_no_temp_file_when_write_fails(isolated, monkeypatch):
    path = isolated / "cfg" / "env"

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        config.set_config("XAI_API_KEY", "secret", path)
    assert not [p for p in path.parent.iterdir() if ".tmp" in p.name]


def test_env_key_offer_defaults_to_no(isolated, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-fromenv")
    answers = []
    code, _ = run(interactive=True, ask=lambda _p: answers.append(_p) or "", ask_secret=lambda _p: "")
    assert code == 0
    assert not (isolated / "cfg" / "env").exists()
