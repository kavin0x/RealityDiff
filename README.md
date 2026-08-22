# Reality Diff

[![version](https://img.shields.io/badge/version-0.3.0-b6e388?style=flat-square)](https://github.com/kavin0x/RealityDiff)
[![python](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/packaging-uv-DE5FE9?style=flat-square)](https://docs.astral.sh/uv/)
[![xAI](https://img.shields.io/badge/AI-xAI%20Grok%204.3-000000?style=flat-square)](https://docs.x.ai/)
[![pytest](https://img.shields.io/badge/tests-pytest-06A77D?style=flat-square&logo=pytest&logoColor=white)](https://docs.pytest.org/)
[![license](https://img.shields.io/badge/license-MIT-yellow?style=flat-square)](LICENSE)

Git for beliefs.

News feeds overwrite your memory. Reality Diff keeps a git history of a claim: what the internet said, how confident we were, and why that number moved. Diff two weeks, blame a confidence jump, browse the site as of a commit, or revert a rumor panic. That is useful when a story is still unfolding — product launches, policy, markets — and you need a trail, not a vibe.

Give it a claim:

> Apple is going to replace Siri with an LLM.

The system creates a living claim object — evidence for, evidence against, citations, unknowns, predictions, confidence — then watches the internet. When **meaningful** new evidence appears, confidence moves and the reason is committed. Cosmetic rewrites are dropped.

```
claim.commit()
claim.diff()
claim.blame()
claim.revert()
claim.watch()
```

Reasoning and live web/X search use the xAI Responses API (`web_search`, `x_search`). Default model is `grok-4.3` (1M context). Stable system prompts are sent first so **cached inputs** can bill cheaper; if a cached / `previous_response_id` / compacted turn fails, the client falls back to a full uncached request. Large conversations are compacted via `/v1/responses/compact`.

## Run

```bash
export XAI_API_KEY=...
uv sync --extra dev
uv run realitydiff serve
```

Open http://localhost:8000

```bash
uv run realitydiff open "Apple is going to replace Siri with an LLM."
uv run realitydiff watch <claim_id>
uv run realitydiff log <claim_id>
uv run realitydiff diff <claim_id> <sha_a> <sha_b>
uv run realitydiff blame <claim_id>
uv run realitydiff revert <claim_id> <sha>
```

### Background timer (installs a user service)

This installs a LaunchAgent (macOS), systemd user timer (Linux), or scheduled task (Windows) that runs `claim.watch()` on due claims:

```bash
uv run realitydiff service install --interval 300
uv run realitydiff service status
uv run realitydiff service uninstall
```

The UI has the same control: pick **every 1m / 5m / 15m / 1h / 6h / 1d**, then **install timer service**. `claim.watch()` does not lock the rest of the app.

## HTTP

The HTTP surface is REST. Nested claim trees, Git-style mutations, and a GraphQL schema are equally valid shapes for this product; this repo ships REST because commit / diff / blame / revert / browse-at-sha are actions on a history, not only nested reads. A GraphQL query layer can sit on the same `Claim` object without changing the store.

| Method | Path | Action |
| --- | --- | --- |
| POST | `/api/claims` | `open` — survey the internet, initial commit |
| GET | `/api/claims/{id}` | HEAD + working tree |
| GET | `/api/claims/{id}/at/{sha}` | browse the belief as of that commit |
| GET | `/api/claims/{id}/tree` | git parent chain |
| POST | `/api/claims/{id}/watch` | search again; commit only if evidence moved |
| GET | `/api/claims/{id}/diff` | `claim.diff(a, b)` |
| GET | `/api/claims/{id}/blame` | `claim.blame()` |
| POST | `/api/claims/{id}/commit` | `claim.commit()` |
| POST | `/api/claims/{id}/revert` | `claim.revert(sha)` |
| GET | `/api/claims/{id}/log` | history, newest first |
| POST | `/api/claims/{id}/watching` | live watch on/off + interval |
| POST | `/api/service` | install OS timer that autoruns watch |
| DELETE | `/api/service` | uninstall OS timer |

## Tests

```bash
uv run pytest
```
