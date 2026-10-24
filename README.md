# Reality Diff

Git for beliefs — a version history of a claim: evidence, confidence, and why that number moved. For people tracking a story that is still unfolding (products, policy, markets) who want a trail, not a vibe.

## Why I built this

News feeds overwrite your memory. Last week’s confidence on a rumor disappears under today’s take. I wanted git-style operations on a belief: commit when evidence actually moves, diff two weeks, blame a confidence jump, browse the claim as of a SHA, or revert a panic. So I built Reality Diff.

## News feed vs this

| | Typical news / search | Reality Diff |
| --- | --- | --- |
| Memory | Overwritten | Versioned history per claim |
| Confidence | Vibes / headlines | Explicit score + reason when it moves |
| Compare | Manual screenshots | `diff` between commits |
| Undo | None | `revert` to an earlier belief state |

## Features

**Claims**

- Open a claim (e.g. “Apple is going to replace Siri with an LLM”)
- Living object: evidence for/against, unknowns, predictions, confidence
- Commits only when **meaningful** new evidence appears (cosmetic rewrites dropped)

**Git-style ops**

- `commit` · `diff` · `blame` · `revert` · `watch` · `log`
- Browse the belief as of any SHA
- Background watch via OS timer (macOS LaunchAgent / Linux systemd / Windows task) or the UI interval picker

**Surfaces**

- Local web UI (`realitydiff serve` → <http://localhost:8000>)
- CLI for agents and scripts
- REST API for the same actions

## Install

From PyPI (needs Python 3.11+). `uv` and `pipx` each give it its own isolated environment, so use one of them first:

```bash
uv tool install realitydiff     # or: pipx install realitydiff
realitydiff                     # first run walks you through setup
```

Plain `pip install realitydiff` works inside a virtualenv. On macOS and many Linux setups, system-wide `pip` refuses with an "externally-managed-environment" error, which is why I lead with `uv`/`pipx`.

The first time you run `realitydiff` in a terminal (or any time you run `realitydiff setup`), it does what the old installer did: checks the install, offers to save your `XAI_API_KEY` to `~/.config/realitydiff/env` (permissions 600, typing is hidden), and tells you if the command isn't on your PATH. `realitydiff setup --yes` never prompts, and `--no-key` skips the key.

Prefer a script that also installs `uv` for you (macOS or Linux, no sudo)?

```bash
curl -fsSL https://raw.githubusercontent.com/kavin0x/RealityDiff/main/install.sh | sh
```

Read it first if you like: download `install.sh`, then run `sh install.sh --help`. Options go after `sh -s --`: `--yes`, `--ref <tag-or-commit>`, `--uninstall`.

Want the latest unreleased code instead of PyPI?

```bash
uv tool install "realitydiff @ https://github.com/kavin0x/RealityDiff/archive/main.tar.gz"
```

Then:

```bash
realitydiff serve                       # binds to 127.0.0.1 only; open http://127.0.0.1:8000
```

The installed CLI keeps claims in `~/.local/share/realitydiff/` (set `REALITYDIFF_DB` to change it). Inside a checkout it still uses `data/`.

## Try it (from a checkout)

```bash
export XAI_API_KEY=...
uv sync --extra dev
uv run realitydiff serve
# open http://localhost:8000
```

```bash
uv run realitydiff open "Apple is going to replace Siri with an LLM."
uv run realitydiff watch <claim_id>
uv run realitydiff log <claim_id>
uv run realitydiff diff <claim_id> <sha_a> <sha_b>
```

```bash
uv run realitydiff service install --interval 300
uv run pytest
```

## Stack

Python 3.11+ · uv · local web UI + REST · xAI Responses API (web + X search) · MIT

## License

[MIT](LICENSE)
