"""User config for installed copies of Reality Diff.

The installer can save settings to ``~/.config/realitydiff/env`` (or
``$XDG_CONFIG_HOME/realitydiff/env``). Values already set in the
environment always win over the file.
"""

from __future__ import annotations

import os
from pathlib import Path

ALLOWED_KEYS = ("XAI_API_KEY", "REALITYDIFF_DB")


def config_file() -> Path:
    override = os.environ.get("REALITYDIFF_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "realitydiff" / "env"


def load_env(path: Path | None = None) -> None:
    """Read KEY=VALUE lines into os.environ without overriding existing values."""
    path = path or config_file()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key in ALLOWED_KEYS and value:
            found[key] = value  # last line wins, like sourcing the file
    for key, value in found.items():
        if not os.environ.get(key):
            os.environ[key] = value


def default_db() -> str:
    """Where claims are stored.

    REALITYDIFF_DB wins. Inside a Reality Diff checkout (or wherever an old
    ``data/realitydiff.sqlite`` already exists) keep that path; otherwise use
    the user data dir so an installed CLI does not drop a data/ folder
    wherever it is run.
    """
    env = os.environ.get("REALITYDIFF_DB")
    if env:
        return env
    legacy = Path("data") / "realitydiff.sqlite"
    in_checkout = Path("pyproject.toml").is_file() and Path("src", "realitydiff").is_dir()
    if legacy.exists() or in_checkout:
        return "data/realitydiff.sqlite"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return str(Path(base) / "realitydiff" / "realitydiff.sqlite")
