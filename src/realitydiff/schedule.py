from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path


LABEL = "com.realitydiff.watch"
WINDOWS_TASK = "RealityDiffWatch"


def install(interval_seconds: int, *, db: str, env: dict[str, str] | None = None) -> dict[str, str]:
    if interval_seconds < 30:
        raise ValueError("Interval must be at least 30 seconds")
    env = {**_watch_env(db), **(env or {})}
    system = platform.system()
    if system == "Darwin":
        path = _macos_plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_macos_plist(interval_seconds, env), encoding="utf-8")
        _run(["launchctl", "unload", str(path)], check=False)
        _run(["launchctl", "load", str(path)])
        return {"platform": "darwin", "path": str(path), "interval_seconds": str(interval_seconds)}
    if system == "Linux":
        unit_dir = Path.home() / ".config/systemd/user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        service = unit_dir / "realitydiff-watch.service"
        timer = unit_dir / "realitydiff-watch.timer"
        service.write_text(_systemd_service(env), encoding="utf-8")
        timer.write_text(_systemd_timer(interval_seconds), encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])
        _run(["systemctl", "--user", "enable", "--now", "realitydiff-watch.timer"])
        return {"platform": "linux", "path": str(timer), "interval_seconds": str(interval_seconds)}
    if system == "Windows":
        minutes = max(1, round(interval_seconds / 60))
        script = _windows_script_path()
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(_windows_cmd(env), encoding="utf-8")
        _run(
            [
                "schtasks",
                "/Create",
                "/TN",
                WINDOWS_TASK,
                "/SC",
                "MINUTE",
                "/MO",
                str(minutes),
                "/TR",
                f'"{script}"',
                "/F",
            ]
        )
        return {"platform": "windows", "path": str(script), "interval_seconds": str(minutes * 60)}
    raise RuntimeError(f"No service installer for {system}")


def uninstall() -> dict[str, str]:
    system = platform.system()
    if system == "Darwin":
        path = _macos_plist_path()
        _run(["launchctl", "unload", str(path)], check=False)
        if path.exists():
            path.unlink()
        return {"platform": "darwin", "removed": str(path)}
    if system == "Linux":
        _run(["systemctl", "--user", "disable", "--now", "realitydiff-watch.timer"], check=False)
        unit_dir = Path.home() / ".config/systemd/user"
        for name in ("realitydiff-watch.service", "realitydiff-watch.timer"):
            path = unit_dir / name
            if path.exists():
                path.unlink()
        return {"platform": "linux", "removed": str(unit_dir / "realitydiff-watch.timer")}
    if system == "Windows":
        _run(["schtasks", "/Delete", "/TN", WINDOWS_TASK, "/F"], check=False)
        script = _windows_script_path()
        if script.exists():
            script.unlink()
        return {"platform": "windows", "removed": str(script)}
    raise RuntimeError(f"No service installer for {system}")


def status() -> dict[str, str]:
    system = platform.system()
    if system == "Darwin":
        path = _macos_plist_path()
        return {"platform": "darwin", "installed": str(path.exists()).lower(), "path": str(path)}
    if system == "Linux":
        timer = Path.home() / ".config/systemd/user/realitydiff-watch.timer"
        return {"platform": "linux", "installed": str(timer.exists()).lower(), "path": str(timer)}
    if system == "Windows":
        result = _run(["schtasks", "/Query", "/TN", WINDOWS_TASK], check=False)
        installed = result.returncode == 0
        return {"platform": "windows", "installed": str(installed).lower(), "path": WINDOWS_TASK}
    return {"platform": system.lower(), "installed": "false"}


def _watch_env(db: str) -> dict[str, str]:
    env = {
        "REALITYDIFF_DB": str(Path(db).expanduser().resolve()),
        "PATH": os.environ.get("PATH", ""),
    }
    if os.environ.get("XAI_API_KEY"):
        env["XAI_API_KEY"] = os.environ["XAI_API_KEY"]
    return env


def _windows_script_path() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home()) / "RealityDiff"
    return base / "watch-all.cmd"


def _bat_escape(value: str) -> str:
    return (
        str(value)
        .replace("%", "%%")
        .replace("^", "^^")
        .replace("&", "^&")
        .replace("|", "^|")
        .replace("<", "^<")
        .replace(">", "^>")
        .replace('"', "")
    )


def _windows_cmd(env: dict[str, str]) -> str:
    lines = ["@echo off"]
    for key, value in env.items():
        lines.append(f'set "{key}={_bat_escape(value)}"')
    exe, *args = _program_args()
    quoted = " ".join(f'"{part}"' for part in [exe, *args])
    lines.append(quoted)
    return "\n".join(lines) + "\n"


def _macos_plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def _program_args() -> list[str]:
    return [sys.executable, "-m", "realitydiff", "watch-all"]


def _macos_plist(interval_seconds: int, env: dict[str, str]) -> str:
    args = "".join(f"        <string>{_xml(arg)}</string>\n" for arg in _program_args())
    env_xml = "".join(f"        <key>{_xml(k)}</key>\n        <string>{_xml(v)}</string>\n" for k, v in env.items())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args}    </array>
    <key>StartInterval</key>
    <integer>{int(interval_seconds)}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
{env_xml}    </dict>
</dict>
</plist>
"""


def _systemd_service(env: dict[str, str]) -> str:
    env_lines = "\n".join(f"Environment={k}={v}" for k, v in env.items())
    exe, *args = _program_args()
    return f"""[Unit]
Description=Reality Diff claim.watch()
[Service]
Type=oneshot
ExecStart={exe} {" ".join(args)}
{env_lines}
"""


def _systemd_timer(interval_seconds: int) -> str:
    return f"""[Unit]
Description=Reality Diff watch timer
[Timer]
OnBootSec=30
OnUnitActiveSec={int(interval_seconds)}
AccuracySec=10
Persistent=true
[Install]
WantedBy=timers.target
"""


def _xml(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)
