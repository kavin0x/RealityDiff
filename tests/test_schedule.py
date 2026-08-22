from realitydiff.schedule import _macos_plist, _windows_cmd, install


def test_macos_plist_contains_interval_and_watch_all():
    xml = _macos_plist(300, {"XAI_API_KEY": "test", "REALITYDIFF_DB": "/tmp/x.sqlite"})
    assert "<integer>300</integer>" in xml
    assert "watch-all" in xml
    assert "XAI_API_KEY" in xml


def test_install_writes_plist(monkeypatch, tmp_path):
    plist = tmp_path / "com.realitydiff.watch.plist"
    monkeypatch.setattr("realitydiff.schedule.platform.system", lambda: "Darwin")
    monkeypatch.setattr("realitydiff.schedule._macos_plist_path", lambda: plist)
    monkeypatch.setattr("realitydiff.schedule._run", lambda *args, **kwargs: None)
    result = install(120, db=str(tmp_path / "db.sqlite"), env={"XAI_API_KEY": "k"})
    assert plist.exists()
    assert result["interval_seconds"] == "120"
    assert "watch-all" in plist.read_text(encoding="utf-8")


def test_windows_cmd_includes_watch_env():
    script = _windows_cmd(
        {
            "REALITYDIFF_DB": r"C:\Users\me\data\realitydiff.sqlite",
            "XAI_API_KEY": "secret-key",
            "PATH": r"C:\Python;C:\Windows",
        }
    )
    assert "set \"REALITYDIFF_DB=" in script
    assert r"C:\Users\me\data\realitydiff.sqlite" in script
    assert "set \"XAI_API_KEY=secret-key\"" in script
    assert "-m" in script
    assert "watch-all" in script


def test_install_writes_windows_wrapper(monkeypatch, tmp_path):
    script = tmp_path / "watch-all.cmd"
    captured = {}

    def fake_run(cmd, *, check=True):
        captured["cmd"] = cmd
        return None

    monkeypatch.setattr("realitydiff.schedule.platform.system", lambda: "Windows")
    monkeypatch.setattr("realitydiff.schedule._windows_script_path", lambda: script)
    monkeypatch.setattr("realitydiff.schedule._run", fake_run)
    monkeypatch.setenv("XAI_API_KEY", "k")
    result = install(300, db=str(tmp_path / "db.sqlite"))
    text = script.read_text(encoding="utf-8")
    assert script.exists()
    assert "REALITYDIFF_DB" in text
    assert "XAI_API_KEY" in text
    assert "watch-all" in text
    assert captured["cmd"][1] == "/Create"
    assert f'"{script}"' in captured["cmd"]
    assert result["path"] == str(script)
