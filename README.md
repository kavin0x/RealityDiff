# Reality Diff

Git for beliefs — a version history of a claim: evidence, confidence, and why that number moved. For people tracking a story that is still unfolding (products, policy, markets) who want a trail, not a vibe.

## Why I built this

News feeds overwrite your memory. Last week’s confidence on a rumor disappears under today’s take. I wanted git-style operations on a belief: commit when evidence actually moves, diff two weeks, blame a confidence jump, browse the claim as of a SHA, or revert a panic. So I built Reality Diff.

## News feed vs this

| | Typical news / search | Reality Diff |
|---|---|---|
| Memory | Overwritten | Versioned history per claim |
| Confidence | Vibes / headlines | Explicit score + reason when it moves |
| Compare | Manual screenshots | `diff` between commits |
| Undo | None | `revert` to an earlier belief state |

## Features

**Claims**
- Open a claim (e.g. “Apple is going to replace Siri with an LLM”)
- Living object: evidence for/against, citations, unknowns, predictions, confidence
- Commits only when **meaningful** new evidence appears (cosmetic rewrites dropped)

**Git-style ops**
- `commit` · `diff` · `blame` · `revert` · `watch` · `log`
- Browse the belief as of any SHA
- Background watch via OS timer (macOS LaunchAgent / Linux systemd / Windows task) or the UI interval picker

**Surfaces**
- Local web UI (`realitydiff serve` → http://localhost:8000)
- CLI for agents and scripts
- REST API for the same actions

## Try it

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
