from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from realitydiff import __version__
from realitydiff.config import config_file, default_db, load_env
from realitydiff.setup import needs_first_run, run_setup
from realitydiff.service import RealityDiff
from realitydiff.xai import XAIError

load_env()
DEFAULT_DB = default_db()


def _db_path() -> str:
    return str(Path(DEFAULT_DB).expanduser().resolve())


def _engine() -> RealityDiff:
    return RealityDiff.open(DEFAULT_DB)


def _print(data) -> None:
    if hasattr(data, "model_dump"):
        print(json.dumps(data.model_dump(mode="json"), indent=2, default=str))
    else:
        print(json.dumps(data, indent=2, default=str))


def _has_provider_key() -> bool:
    return bool(os.environ.get("XAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY"))


def _ensure_key(*, need_xai: bool = False) -> bool:
    """True when a required API key is available. Offers first-run setup at a terminal."""
    if need_xai and os.environ.get("XAI_API_KEY"):
        return True
    if not need_xai and _has_provider_key():
        return True
    if needs_first_run():
        print("realitydiff: first run, so let's set up.\n", file=sys.stderr)
        run_setup()
        print()
        load_env()
        if need_xai and os.environ.get("XAI_API_KEY"):
            return True
        if not need_xai and _has_provider_key():
            return True
    if need_xai:
        print(
            f"realitydiff: XAI_API_KEY is not set. Run `realitydiff setup`, export it, or save it in {config_file()}",
            file=sys.stderr,
        )
    else:
        print(
            f"realitydiff: no API key. Set XAI_API_KEY and/or OPENROUTER_API_KEY, run `realitydiff setup`, or save them in {config_file()}",
            file=sys.stderr,
        )
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="realitydiff", description="Git for beliefs")
    parser.add_argument("--version", action="version", version=f"realitydiff {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    setup_p = sub.add_parser("setup", help="Guided setup: save your xAI key and check your PATH")
    setup_p.add_argument("-y", "--yes", action="store_true", help="Non-interactive: accept defaults, never prompt")
    setup_p.add_argument("--no-key", action="store_true", help="Skip the API key prompts")

    open_p = sub.add_parser("open", help="Create a living claim and survey the internet")
    open_p.add_argument("statement")
    open_p.add_argument("--model", help="xAI model id (overrides XAI_MODEL / the default fallback list)")

    compare_p = sub.add_parser("compare", help="Ask the same question of several models")
    compare_p.add_argument("question")
    compare_p.add_argument(
        "-m",
        "--model",
        action="append",
        dest="models",
        help="provider:model_id, repeatable (xai:grok-4.3 or openrouter:openai/gpt-4o-mini)",
    )
    compare_p.add_argument("--timeout", type=float, default=45.0, help="Per-model timeout in seconds (max 120)")

    watch_p = sub.add_parser("watch", help="Search for new evidence and maybe commit")
    watch_p.add_argument("claim_id")
    watch_p.add_argument("--model", help="xAI model id (overrides XAI_MODEL / the default fallback list)")

    sub.add_parser("watch-all", help="Watch every due claim once (used by the installed service)")

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
    serve_p.add_argument("--host", default="127.0.0.1", help="Bind address. Use 0.0.0.0 only if you want other machines to reach it")
    serve_p.add_argument("--port", type=int, default=8000)

    svc = sub.add_parser("service", help="Install a background timer that runs claim.watch()")
    svc_sub = svc.add_subparsers(dest="service_cmd", required=True)
    inst = svc_sub.add_parser("install", help="Install a LaunchAgent / systemd user timer / Windows task")
    inst.add_argument("--interval", type=int, default=300, help="Seconds between watches (min 30)")
    svc_sub.add_parser("uninstall", help="Remove the background watch service")
    svc_sub.add_parser("status", help="Show whether the service is installed")

    args = parser.parse_args(argv)
    if args.cmd is None:
        if needs_first_run():
            return run_setup()
        parser.print_help()
        return 0
    if args.cmd == "setup":
        return run_setup(assume_yes=args.yes, skip_key=args.no_key)
    if args.cmd == "serve":
        if not _ensure_key(need_xai=False):
            return 2
        import uvicorn

        print(f"realitydiff: database {_db_path()}", file=sys.stderr)
        uvicorn.run("realitydiff.api:create_app", host=args.host, port=args.port, factory=True, reload=False)
        return 0

    if args.cmd == "service":
        from realitydiff.schedule import install, status, uninstall

        if args.service_cmd == "install":
            _print(install(args.interval, db=DEFAULT_DB))
            return 0
        if args.service_cmd == "uninstall":
            _print(uninstall())
            return 0
        _print({**status(), "db": _db_path()})
        return 0

    if args.cmd == "compare":
        if not _ensure_key(need_xai=False):
            return 2
        from realitydiff.compare import run_compare
        from realitydiff.store import BeliefStore

        models = args.models or []
        if not models:
            print("realitydiff: pass at least one -m provider:model_id", file=sys.stderr)
            return 2
        try:
            import asyncio

            payload = asyncio.run(
                run_compare(
                    args.question,
                    models,
                    timeout=args.timeout,
                    store=BeliefStore(DEFAULT_DB),
                )
            )
        except ValueError as exc:
            print(f"realitydiff: {exc}", file=sys.stderr)
            return 2
        _print(payload)
        return 0

    if not _ensure_key(need_xai=True):
        return 2
    try:
        from realitydiff.providers import default_xai_models
        from realitydiff.xai import XAIClient

        model_id = getattr(args, "model", None)
        models = (model_id,) if model_id else default_xai_models()
        engine = RealityDiff.open(DEFAULT_DB, model=XAIClient(models=models))
    except XAIError as exc:
        print(f"realitydiff: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "watch-all":
        _print([item.model_dump(mode="json") for item in engine.watch_due()])
        return 0
    if args.cmd == "open":
        living, commit = engine.open_claim(args.statement)
        record = living.refresh()
        _print(
            {
                "claim_id": living.id,
                "commit": commit.id,
                "confidence": commit.new_confidence,
                "reason": commit.reason,
                "closed": record.closed,
                "outcome_verdict": record.outcome_verdict,
            }
        )
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
