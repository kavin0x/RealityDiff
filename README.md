# Reality Diff

Git for beliefs: a version history of a claim (evidence, confidence, and why that number moved). For people tracking a story that is still unfolding (products, policy, markets) who want a trail, not a vibe. This is alpha. Claims still depend on xAI search; compare is a thin side-by-side, not a second reasoner.

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

- Local web UI (`realitydiff serve` → <http://127.0.0.1:8000>)
- Compare view: one question, several models, side by side (xAI and/or OpenRouter)
- CLI for agents and scripts
- REST API for the same actions

## Install

From PyPI (needs Python 3.11+). `uv` and `pipx` each give it its own isolated environment, so use one of them first:

```bash
uv tool install realitydiff     # or: pipx install realitydiff
realitydiff                     # first run walks you through setup
```

Plain `pip install realitydiff` works inside a virtualenv. On macOS and many Linux setups, system-wide `pip` refuses with an "externally-managed-environment" error, which is why I lead with `uv`/`pipx`.

The first time you run `realitydiff` in a terminal (or any time you run `realitydiff setup`), it checks the install, offers to save your `XAI_API_KEY` to `~/.config/realitydiff/env` (permissions 600, typing is hidden), optionally offers an `OPENROUTER_API_KEY` the same way (default no), and tells you if the command isn't on your PATH. `realitydiff setup --yes` never prompts, and `--no-key` skips both keys.

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
export XAI_API_KEY=...                 # claims / watch (xAI Responses API)
export OPENROUTER_API_KEY=...          # optional; compare via OpenRouter
# or put the same keys in ~/.config/realitydiff/env (mode 600)
# optional: XAI_MODEL=grok-4.3  OPENROUTER_MODEL=openai/gpt-4o-mini
uv sync --extra dev
uv run realitydiff serve               # 127.0.0.1:8000
# open http://127.0.0.1:8000 then click "compare models"
```

The web UI and API have no login. `serve` binds to 127.0.0.1 by default; if you pass `--host 0.0.0.0`, anyone on your network can run compare and watch on your API keys and spend your credits. Keys never leave the server, but the calls they pay for can.

### Compare spend limits

I put a few brakes on compare so one bad afternoon can't drain a key:

- Every chat call asks for at most 1024 output tokens. Change it with `REALITYDIFF_MAX_TOKENS` (I clamp it to 16 through 8192).
- At most 3 model calls run at the same time across all compare requests. Extra ones wait their turn.
- At most 30 model calls per minute in total (a 6 model compare counts as 6). Past that, the API answers 429 with a `Retry-After` header. Change it with `REALITYDIFF_COMPARE_PER_MINUTE` (1 through 600).
- If you set `REALITYDIFF_COMPARE_MODELS`, only those models can run. It's comma separated: `openrouter:openai/gpt-4o-mini` pins a provider, a bare id like `grok-4.3` works on either one. Leave it unset and any model id goes.

```bash
export REALITYDIFF_MAX_TOKENS=1024
export REALITYDIFF_COMPARE_PER_MINUTE=30
export REALITYDIFF_COMPARE_MODELS="openrouter:openai/gpt-4o-mini,grok-4.3"
```

These can also go in `~/.config/realitydiff/env`. All of this lives in the app, so it only helps while the app is the thing spending. I'd also set a credit limit on the OpenRouter key itself (openrouter.ai, Keys page, edit the key) so the cap holds even if something else gets the key.

```bash
uv run realitydiff open "Apple is going to replace Siri with an LLM."
uv run realitydiff open --model grok-4.3 "..."
uv run realitydiff watch <claim_id>
uv run realitydiff log <claim_id>
uv run realitydiff diff <claim_id> <sha_a> <sha_b>
uv run realitydiff compare "Will Apple replace Siri this year?" \
  -m xai:grok-4.3 -m openrouter:openai/gpt-4o-mini
```

```bash
uv run realitydiff service install --interval 300
uv run pytest
```

## Stack

Python 3.11+ · uv · local web UI + REST · xAI Responses API (web + X search) · OpenRouter chat completions (optional, compare only) · MIT

## License

[MIT](LICENSE)
