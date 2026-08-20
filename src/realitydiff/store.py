from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from realitydiff.hashing import sha1_hex
from realitydiff.models import ClaimRecord, ClaimState, Commit, utcnow


SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    watching INTEGER NOT NULL DEFAULT 1,
    watch_interval_seconds INTEGER NOT NULL DEFAULT 300,
    head TEXT,
    last_watched_at TEXT,
    working_json TEXT
);

CREATE TABLE IF NOT EXISTS commits (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    parent_id TEXT,
    tree_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    author TEXT NOT NULL,
    message TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    previous_confidence REAL,
    new_confidence REAL NOT NULL,
    state_json TEXT NOT NULL,
    FOREIGN KEY(claim_id) REFERENCES claims(id)
);

CREATE INDEX IF NOT EXISTS idx_commits_claim ON commits(claim_id, created_at);
"""


class BeliefStore:
    """Content-addressed belief repository. Commits are immutable."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def create_claim(self, statement: str, *, claim_id: str | None = None) -> ClaimRecord:
        created = utcnow()
        cid = claim_id or f"cl_{sha1_hex(statement + created.isoformat())[:12]}"
        record = ClaimRecord(id=cid, created_at=created, title=statement.strip())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO claims (id, created_at, title, watching, watch_interval_seconds, head, working_json)
                VALUES (?, ?, ?, 1, 300, NULL, ?)
                """,
                (record.id, record.created_at.isoformat(), record.title, None),
            )
            self._conn.commit()
        return record

    def get_claim(self, claim_id: str) -> ClaimRecord | None:
        row = self._conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if row:
            return self._claim_from_row(row)
        if len(claim_id) >= 8:
            rows = self._conn.execute("SELECT * FROM claims WHERE id LIKE ?", (claim_id + "%",)).fetchall()
            if len(rows) == 1:
                return self._claim_from_row(rows[0])
        return None

    def list_claims(self) -> list[ClaimRecord]:
        rows = self._conn.execute("SELECT * FROM claims ORDER BY created_at DESC").fetchall()
        return [self._claim_from_row(row) for row in rows]

    def set_watching(self, claim_id: str, watching: bool, interval: int | None = None) -> None:
        if interval is None:
            self._conn.execute("UPDATE claims SET watching = ? WHERE id = ?", (int(watching), claim_id))
        else:
            self._conn.execute(
                "UPDATE claims SET watching = ?, watch_interval_seconds = ? WHERE id = ?",
                (int(watching), interval, claim_id),
            )
        self._conn.commit()

    def mark_watched(self, claim_id: str, when: datetime | None = None) -> None:
        stamp = (when or utcnow()).isoformat()
        self._conn.execute("UPDATE claims SET last_watched_at = ? WHERE id = ?", (stamp, claim_id))
        self._conn.commit()

    def working_state(self, claim_id: str) -> ClaimState | None:
        row = self._conn.execute("SELECT working_json, head FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if not row:
            return None
        if row["working_json"]:
            return ClaimState.model_validate_json(row["working_json"])
        if row["head"]:
            commit = self.get_commit(row["head"])
            return commit.state if commit else None
        return None

    def set_working_state(self, claim_id: str, state: ClaimState | None) -> None:
        payload = state.model_dump_json() if state else None
        self._conn.execute("UPDATE claims SET working_json = ? WHERE id = ?", (payload, claim_id))
        self._conn.commit()

    def head_commit(self, claim_id: str) -> Commit | None:
        claim = self.get_claim(claim_id)
        if not claim or not claim.head:
            return None
        return self.get_commit(claim.head)

    def delete_claim(self, claim_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM commits WHERE claim_id = ?", (claim_id,))
            self._conn.execute("DELETE FROM claims WHERE id = ?", (claim_id,))
            self._conn.commit()

    def get_commit(self, commit_id: str, *, claim_id: str | None = None) -> Commit | None:
        row = self._conn.execute("SELECT * FROM commits WHERE id = ?", (commit_id,)).fetchone()
        if row:
            commit = self._commit_from_row(row)
            if claim_id and commit.claim_id != claim_id:
                return None
            return commit
        if len(commit_id) < 7:
            return None
        sql = "SELECT * FROM commits WHERE id LIKE ?"
        params: list[str] = [commit_id + "%"]
        if claim_id:
            sql += " AND claim_id = ?"
            params.append(claim_id)
        rows = self._conn.execute(sql, params).fetchall()
        if len(rows) == 1:
            return self._commit_from_row(rows[0])
        if len(rows) > 1:
            raise ValueError(f"Ambiguous commit prefix {commit_id}")
        return None

    def log(self, claim_id: str) -> list[Commit]:
        commits: list[Commit] = []
        claim = self.get_claim(claim_id)
        current = claim.head if claim else None
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            commit = self.get_commit(current)
            if not commit:
                break
            commits.append(commit)
            current = commit.parent_id
        return commits

    def append_commit(self, commit: Commit) -> Commit:
        with self._lock:
            existing = self.get_commit(commit.id)
            if existing:
                self._conn.execute("UPDATE claims SET head = ?, working_json = NULL WHERE id = ?", (commit.id, commit.claim_id))
                self._conn.commit()
                return existing
            self._conn.execute(
                """
                INSERT INTO commits (
                    id, claim_id, parent_id, tree_hash, created_at, author, message, reason,
                    previous_confidence, new_confidence, state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    commit.id,
                    commit.claim_id,
                    commit.parent_id,
                    commit.tree_hash,
                    commit.created_at.isoformat(),
                    commit.author,
                    commit.message,
                    commit.reason,
                    commit.previous_confidence,
                    commit.new_confidence,
                    commit.state.model_dump_json(),
                ),
            )
            self._conn.execute(
                "UPDATE claims SET head = ?, working_json = NULL WHERE id = ?",
                (commit.id, commit.claim_id),
            )
            self._conn.commit()
        return commit

    def _claim_from_row(self, row: sqlite3.Row) -> ClaimRecord:
        return ClaimRecord(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            title=row["title"],
            watching=bool(row["watching"]),
            watch_interval_seconds=row["watch_interval_seconds"],
            head=row["head"],
            last_watched_at=datetime.fromisoformat(row["last_watched_at"])
            if row["last_watched_at"]
            else None,
        )

    def _commit_from_row(self, row: sqlite3.Row) -> Commit:
        return Commit(
            id=row["id"],
            claim_id=row["claim_id"],
            parent_id=row["parent_id"],
            tree_hash=row["tree_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            author=row["author"],
            message=row["message"],
            reason=row["reason"],
            previous_confidence=row["previous_confidence"],
            new_confidence=row["new_confidence"],
            state=ClaimState.model_validate_json(row["state_json"]),
        )
