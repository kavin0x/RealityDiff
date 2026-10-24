"""Interactive first-run setup: the same flow install.sh gives, for pip/pipx/uv installs."""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from realitydiff import __version__
from realitydiff.config import (
    config_file,
    config_has,
    default_db,
    set_config,
    setup_marker,
    valid_secret,
)

ART = r"""
  ___          _ _ _        ___  _  __  __
 | _ \___ __ _| (_) |_ _  _|   \(_)/ _|/ _|
 |   / -_) _` | | |  _| || | |) | |  _|  _|
 |_|_\___\__,_|_|_|\__|\_, |___/|_|_| |_|
                       |__/
"""


class _Style:
    def __init__(self, out) -> None:
        fancy = out.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM", "dumb") != "dumb"
        self.fancy = fancy
        esc = "\033["
        self.bold = f"{esc}1m" if fancy else ""
        self.dim = f"{esc}2m" if fancy else ""
        self.red = f"{esc}31m" if fancy else ""
        self.green = f"{esc}32m" if fancy else ""
        self.yellow = f"{esc}33m" if fancy else ""
        self.cyan = f"{esc}36m" if fancy else ""
        self.reset = f"{esc}0m" if fancy else ""
        self.ok_mark = "✓" if fancy else "[ok]"


def _tilde(path: str | Path) -> str:
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home) :] if text.startswith(home + os.sep) else text


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def needs_first_run() -> bool:
    """True when nothing has configured this install yet and a person is at the keyboard."""
    if not is_interactive():
        return False
    if os.environ.get("XAI_API_KEY") or config_has("XAI_API_KEY"):
        return False
    return not setup_marker().exists()


def _confirm(question: str, default: bool, style: _Style, assume_yes: bool, interactive: bool, ask) -> bool:
    if assume_yes or not interactive:
        return default
    hint = "[Y/n]" if default else "[y/N]"
    answer = ask(f"  {style.cyan}?{style.reset} {question} {hint} ").strip().lower()
    if answer in {"y", "yes"}:
        return True
    if answer in {"n", "no"}:
        return False
    return default


def _bin_dir() -> Path | None:
    exe = shutil.which("realitydiff")
    if exe:
        return Path(exe).parent
    script = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if script and script.name.startswith("realitydiff"):
        return script.parent
    return None


def run_setup(
    *,
    assume_yes: bool = False,
    skip_key: bool = False,
    out=None,
    ask=input,
    ask_secret=getpass.getpass,
    interactive: bool | None = None,
) -> int:
    out = out or sys.stdout
    style = _Style(out)
    interactive = is_interactive() if interactive is None else interactive
    if assume_yes:
        interactive = False
    config = config_file()

    def say(text: str = "") -> None:
        print(text, file=out)

    def ok(text: str) -> None:
        say(f"  {style.green}{style.ok_mark}{style.reset} {text}")

    def info(text: str) -> None:
        say(f"  {style.dim}{text}{style.reset}")

    def warn(text: str) -> None:
        say(f"  {style.yellow}!{style.reset} {text}")

    def step(n: int, total: int, title: str) -> None:
        say(f"{style.dim}[{n}/{total}]{style.reset} {style.bold}{title}{style.reset}")

    if style.fancy:
        say(f"{style.bold}{style.cyan}{ART}{style.reset}  {style.dim}git for beliefs{style.reset}\n")
    else:
        say("Reality Diff setup")
        say()

    step(1, 3, "Checking the install")
    ok(f"realitydiff {__version__} on Python {sys.version_info.major}.{sys.version_info.minor}")
    info(f"data   {_tilde(default_db())}")
    info(f"config {_tilde(config)}")

    step(2, 3, "xAI API key")
    saved = False
    if skip_key:
        info("skipped (--no-key)")
    else:
        have_file = config_has("XAI_API_KEY")
        env_key = os.environ.get("XAI_API_KEY", "")
        new_key = ""
        if have_file:
            ok(f"already saved in {_tilde(config)}")
            if interactive and _confirm("Replace the saved key?", False, style, assume_yes, interactive, ask):
                new_key = ask_secret("  ? New XAI_API_KEY (input hidden): ").strip()
        elif interactive:
            if env_key:
                info("XAI_API_KEY is set in this shell")
                if _confirm(
                    f"Save it to {_tilde(config)} so realitydiff always finds it?", False, style, assume_yes, interactive, ask
                ):
                    new_key = env_key
            elif _confirm(
                "Save an XAI_API_KEY now? (get one at https://console.x.ai)", True, style, assume_yes, interactive, ask
            ):
                new_key = ask_secret("  ? XAI_API_KEY (input hidden): ").strip()
        else:
            info("not saved (non-interactive). Export XAI_API_KEY or run `realitydiff setup`.")
        if new_key:
            if valid_secret(new_key):
                set_config("XAI_API_KEY", new_key, config)
                ok(f"saved to {_tilde(config)} (permissions 600)")
                saved = True
            else:
                warn("that key has unexpected characters (spaces, quotes or newlines?); not saved")
        elif not have_file and interactive:
            info("skipped. Run `realitydiff setup` any time, or export XAI_API_KEY.")

    step(3, 3, "PATH")
    bin_dir = _bin_dir()
    on_path = bool(shutil.which("realitydiff"))
    if on_path:
        ok(f"`realitydiff` is on your PATH ({_tilde(bin_dir) if bin_dir else 'found'})")
    elif bin_dir is not None:
        warn(f"{_tilde(bin_dir)} is not on your PATH")
        uv = shutil.which("uv")
        if uv and interactive and _confirm(
            "Add it to your shell config (runs 'uv tool update-shell')?", False, style, assume_yes, interactive, ask
        ):
            subprocess.run([uv, "tool", "update-shell"], check=False)
            info("open a new terminal for it to take effect")
        else:
            info(f'add this to your shell rc:  export PATH="{bin_dir}:$PATH"')
    else:
        info("could not tell where the realitydiff command lives; that is fine if it already runs")

    try:
        marker = setup_marker()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{__version__}\n", encoding="utf-8")
    except OSError:
        pass

    say()
    say(f"{style.green}{style.bold}Reality Diff is set up.{style.reset}")
    say()
    say("  Next:")
    say(f"    {style.bold}realitydiff serve --host 127.0.0.1{style.reset}")
    say(f"    {style.dim}then open http://127.0.0.1:8000{style.reset}")
    say(f'    {style.bold}realitydiff open "Apple will replace Siri with an LLM"{style.reset}')
    say()
    say(f"  {style.dim}Uninstall: pipx uninstall realitydiff  |  uv tool uninstall realitydiff  |  pip uninstall realitydiff{style.reset}")
    return 0
