from __future__ import annotations

from realitydiff.models import BlameEntry, BlameReport, ClaimState, Commit


def blame_history(claim_id: str, history: list[Commit]) -> BlameReport:
    """history is newest-first (git log order)."""
    if not history:
        return BlameReport(claim_id=claim_id, head=None, entries=[])

    chronological = list(reversed(history))
    head = history[0].id
    first_seen: dict[str, tuple[Commit, object]] = {}
    last_seen: dict[str, tuple[Commit, object]] = {}

    def note(path: str, commit: Commit, value: object) -> None:
        if path not in first_seen:
            first_seen[path] = (commit, value)
        last_seen[path] = (commit, value)

    previous: ClaimState | None = None
    for commit in chronological:
        state = commit.state
        if previous is None or previous.statement != state.statement:
            note("statement", commit, state.statement)
        if previous is None or previous.refined_statement != state.refined_statement:
            note("refined_statement", commit, state.refined_statement)
        if previous is None or previous.confidence != state.confidence:
            note("confidence", commit, state.confidence)
        if previous is None or previous.summary != state.summary:
            note("summary", commit, state.summary)

        prev_for = {item.id: item for item in previous.evidence_for} if previous else {}
        prev_against = {item.id: item for item in previous.evidence_against} if previous else {}
        prev_unknowns = {item.id: item for item in previous.unknowns} if previous else {}
        prev_preds = {item.id: item for item in previous.predictions} if previous else {}

        for item in state.evidence_for:
            dumped = item.model_dump(mode="json")
            if prev_for.get(item.id) is None or prev_for[item.id].model_dump(mode="json") != dumped:
                note(f"evidence_for/{item.id}", commit, dumped)
        for item in state.evidence_against:
            dumped = item.model_dump(mode="json")
            if prev_against.get(item.id) is None or prev_against[item.id].model_dump(mode="json") != dumped:
                note(f"evidence_against/{item.id}", commit, dumped)
        for item in state.unknowns:
            dumped = item.model_dump(mode="json")
            if prev_unknowns.get(item.id) is None or prev_unknowns[item.id].model_dump(mode="json") != dumped:
                note(f"unknowns/{item.id}", commit, dumped)
        for item in state.predictions:
            dumped = item.model_dump(mode="json")
            if prev_preds.get(item.id) is None or prev_preds[item.id].model_dump(mode="json") != dumped:
                note(f"predictions/{item.id}", commit, dumped)
        previous = state

    entries: list[BlameEntry] = []
    for path, (introduced, value) in first_seen.items():
        last_commit, last_value = last_seen[path]
        item_id = path.split("/", 1)[1] if "/" in path else None
        entries.append(
            BlameEntry(
                path=path,
                item_id=item_id,
                introduced_in=introduced.id,
                introduced_at=introduced.created_at,
                author=introduced.author,
                message=introduced.message,
                last_changed_in=last_commit.id,
                last_changed_at=last_commit.created_at,
                last_author=last_commit.author,
                last_message=last_commit.message,
                value=last_value if last_value is not None else value,
            )
        )
    entries.sort(key=lambda entry: entry.path)
    return BlameReport(claim_id=claim_id, head=head, entries=entries)
