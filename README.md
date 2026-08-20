# Reality Diff

Git for beliefs.

Give it a claim:

> Apple is going to replace Siri with an LLM.

The system creates a living claim object — evidence for, evidence against, unknowns, predictions, confidence — then watches the internet. When new evidence appears, confidence moves and the reason is committed.

```
claim.commit()
claim.diff()
claim.blame()
claim.revert()
```

Reasoning and live web/X search use the xAI Responses API (`web_search`, `x_search`). Default model is `grok-4.5`, with fallbacks if a model is at capacity.

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

## HTTP

The HTTP surface is REST. Nested claim trees, Git-style mutations, and a GraphQL schema are equally valid shapes for this product; this repo ships REST because commit / diff / blame / revert are actions on a history, not only nested reads. A GraphQL query layer can sit on the same `Claim` object without changing the store.

| Method | Path | Action |
| --- | --- | --- |
| POST | `/api/claims` | `open` — survey the internet, initial commit |
| GET | `/api/claims/{id}` | HEAD + working tree |
| POST | `/api/claims/{id}/watch` | search again; commit if evidence moved |
| GET | `/api/claims/{id}/diff` | `claim.diff(a, b)` |
| GET | `/api/claims/{id}/blame` | `claim.blame()` |
| POST | `/api/claims/{id}/commit` | `claim.commit()` |
| POST | `/api/claims/{id}/revert` | `claim.revert(sha)` |
| GET | `/api/claims/{id}/log` | history, newest first |

## Tests

```bash
uv run pytest
```
