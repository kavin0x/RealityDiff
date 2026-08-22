from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from realitydiff.service import RealityDiff

DEFAULT_DB = os.environ.get("REALITYDIFF_DB", "data/realitydiff.sqlite")


def _engine() -> RealityDiff:
    return RealityDiff.open(DEFAULT_DB)


def _print(data) -> None:
    if hasattr(data, "model_dump"):
        print(json.dumps(data.model_dump(mode="json"), indent=2, default=str))
    else:
        print(json.dumps(data, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="realitydiff", description="Git for beliefs")
    sub = parser.add_subparsers(dest="cmd", required=True)

    open_p = sub.add_parser("open", help="Create a living claim and survey the internet")
    open_p.add_argument("statement")

    watch_p = sub.add_parser("watch", help="Search for new evidence and maybe commit")
    watch_p.add_argument("claim_id")

    log_p = sub.add_parser("log", help="Commit history")
    log_p.add_argument("claim_id")

    show_p = sub.add_parser("show", help="HEAD state")
    show_p.add_argument("claim_id")

    diff_p = sub.add_parser("diff", help="Diff two commits (default HEAD vs WORKING)")
    diff_p.add_argument("claim_id")
    diff_p.add_argument("a", nargs="?")
    diff_p.add_argument("b", nargs="?")

    blame_p = sub.add_parser("blame", help="Who introduced each piece of the belief")
    blame_p.add_argument("claim_id")

    revert_p = sub.add_parser("revert", help="Restore a prior belief snapshot as a new commit")
    revert_p.add_argument("claim_id")
    revert_p.add_argument("sha")

    commit_p = sub.add_parser("commit", help="Commit the working tree")
    commit_p.add_argument("claim_id")
    commit_p.add_argument("-m", "--message", required=True)

    ls_p = sub.add_parser("ls", help="List claims")

    serve_p = sub.add_parser("serve", help="Run the web UI")
    serve_p.add_argument("--host", default="0.0.0.0")
    serve_p.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("realitydiff.api:create_app", host=args.host, port=args.port, factory=True, reload=False)
        return 0

    engine = _engine()
    if args.cmd == "open":
        living, commit = engine.open_claim(args.statement)
        _print({"claim_id": living.id, "commit": commit.id, "confidence": commit.new_confidence, "reason": commit.reason})
        return 0
    if args.cmd == "ls":
        _print([record.model_dump(mode="json") for record in engine.store.list_claims()])
        return 0
    if args.cmd == "watch":
        _print(engine.watch(args.claim_id))
        return 0
    living = engine.claim(args.claim_id)
    if args.cmd == "log":
        _print(
            [
                {
                    "id": c.id,
                    "parent": c.parent_id,
                    "confidence": c.new_confidence,
                    "message": c.message,
                    "reason": c.reason,
                    "author": c.author,
                    "at": c.created_at.isoformat(),
                }
                for c in living.log()
            ]
        )
        return 0
    if args.cmd == "show":
        head = living.head
        if head is None:
            print("no HEAD", file=sys.stderr)
            return 1
        _print(head)
        return 0
    if args.cmd == "diff":
        _print(living.diff(args.a, args.b))
        return 0
    if args.cmd == "blame":
        _print(living.blame())
        return 0
    if args.cmd == "revert":
        _print(living.revert(args.sha))
        return 0
    if args.cmd == "commit":
        _print(living.commit(args.message))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
