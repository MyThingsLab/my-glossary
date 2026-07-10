from __future__ import annotations

import argparse
from pathlib import Path

from mythings.engine import ClaudeCLIEngine, Engine, NoopEngine
from mythings.github import GitHub
from mythings.ledger import Ledger

from myglossary.glossary import Entry, define, load_corpus
from myglossary.tool import BACKLOG_LABEL, Result, Tool


def _render(result: Result) -> str:
    line = f"{result.outcome}: {result.detail}"
    if result.issue is not None:
        line += f" (issue #{result.issue})"
    return line


def _render_entry(entry: Entry) -> str:
    lines = [entry.term, "=" * len(entry.term), ""]
    if entry.definition:
        lines.append(entry.definition)
    else:
        lines.append("(no definition -- the corpus excerpts do not define this term)")
    lines += ["", "Sources:"]
    for c in entry.citations:
        lines.append(f"  {c.marker()} {c.title} (chars {c.start}-{c.end})")
    if entry.unknown_markers:
        # Loud on purpose: the Engine cited an excerpt it was never shown.
        lines += ["", f"WARNING: fabricated citations ignored: {', '.join(entry.unknown_markers)}"]
    return "\n".join(lines)


def _engine(name: str) -> Engine:
    return NoopEngine() if name == "noop" else ClaudeCLIEngine()


def main(argv: list[str] | None = None, *, tool_factory: type[Tool] = Tool) -> int:
    parser = argparse.ArgumentParser(
        prog="myglossary",
        description="Define terms from a document corpus, with citations back to the source.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # The read path: local, prints to stdout, no issue, no PR, no CI.
    define_p = sub.add_parser("define", help="define one term from the corpus and print it")
    define_p.add_argument("term")
    define_p.add_argument(
        "--corpus",
        type=Path,
        action="append",
        required=True,
        help="file or directory of source material (repeatable)",
    )
    define_p.add_argument("--top", type=int, default=8, help="excerpts to shortlist")
    define_p.add_argument("--engine", choices=("noop", "claude"), default="noop")

    # The write path: one labeled issue -> one glossary entry -> a draft PR.
    build = sub.add_parser("build", help="turn one labeled issue into a glossary entry PR")
    build.add_argument("--issue", type=int, help="issue number (default: oldest with the label)")
    build.add_argument("--repo", help="GitHub slug owner/name (defaults to the local remote)")
    build.add_argument("--source", type=Path, default=Path.cwd(), help="local checkout to work in")
    build.add_argument("--corpus", type=Path, action="append", required=True)
    build.add_argument("--top", type=int, default=8)
    build.add_argument("--base", default="main")
    build.add_argument("--label", default=BACKLOG_LABEL)
    build.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    build.add_argument(
        "--engine",
        choices=("noop", "claude"),
        default="noop",
        help="noop replies with a fixed empty string (zero tokens); claude shells out to the CLI",
    )

    args = parser.parse_args(argv)

    if args.cmd == "define":
        documents, chunks = load_corpus(args.corpus)
        if not documents:
            print("no corpus files found")
            return 1
        entry, _ = define(args.term, documents, chunks, _engine(args.engine), top=args.top)
        print(_render_entry(entry))
        return 0

    tool = tool_factory(
        repo=args.source,
        ledger=Ledger(args.ledger),
        github=GitHub(args.repo),
        engine=_engine(args.engine),
        base=args.base,
        label=args.label,
        corpus=args.corpus,
        top=args.top,
    )
    result = tool.run(issue_number=args.issue)
    print(_render(result))
    return 0 if result.outcome not in ("failure", "denied") else 1


if __name__ == "__main__":
    raise SystemExit(main())
