from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mythings.corpus import Chunk, Citation, Document, chunk, cite, ingest, shortlist
from mythings.engine import Engine, EngineRequest

# Text extensions the corpus loader will read directly; PDFs go through
# mythings.corpus.extract (pdftotext). Anything else is ignored rather than
# guessed at.
TEXT_SUFFIXES = frozenset({".md", ".txt", ".rst", ".tex"})
CORPUS_SUFFIXES = TEXT_SUFFIXES | {".pdf"}

_MARKER_RE = re.compile(r"\[([a-z0-9][a-z0-9-]*):(\d+)\]")


@dataclass(frozen=True)
class Entry:
    term: str
    definition: str
    citations: tuple[Citation, ...]
    # Markers the Engine produced that name no excerpt it was shown. Surfaced,
    # never silently dropped: a fabricated citation is the one failure this
    # tool exists to prevent.
    unknown_markers: tuple[str, ...] = ()

    def is_degraded(self) -> bool:
        return not self.definition


def corpus_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(p for p in sorted(path.rglob("*")) if p.suffix.lower() in CORPUS_SUFFIXES)
        elif path.is_file():
            files.append(path)
    return files


def load_corpus(
    paths: Iterable[Path], *, target_chars: int = 1200
) -> tuple[list[Document], list[Chunk]]:
    documents = ingest(corpus_files(paths))
    chunks = [c for doc in documents for c in chunk(doc, target_chars=target_chars)]
    return documents, chunks


def format_excerpts(chunks: Iterable[Chunk], documents: Iterable[Document]) -> str:
    titles = {doc.id: doc.title for doc in documents}
    blocks = []
    for c in chunks:
        body = " ".join(c.text.split())
        blocks.append(f"[{c.doc_id}:{c.ordinal}] ({titles[c.doc_id]})\n{body}")
    return "\n\n".join(blocks)


SYSTEM = (
    "You write glossary definitions for a student, using only the excerpts you are given. "
    "Reply with the definition prose and nothing else -- no preamble, no headings. "
    "Every substantive claim must carry an inline citation marker copied verbatim from an "
    "excerpt heading, like [doc-id:12]. Never invent a marker. If the excerpts do not "
    "define the term, reply with exactly: INSUFFICIENT"
)


def build_prompt(term: str, chunks: Iterable[Chunk], documents: Iterable[Document]) -> str:
    return (
        f"Define the term: {term}\n\n"
        f"Excerpts:\n\n{format_excerpts(chunks, documents)}\n\n"
        f"Definition of {term!r}, citing the excerpts inline:"
    )


def parse_definition(text: str, allowed: Iterable[Chunk]) -> tuple[str, list[str], list[str]]:
    # Returns (definition, cited_markers, unknown_markers). Validation is
    # deterministic and happens after the Engine call, never inside it.
    known = {f"[{c.doc_id}:{c.ordinal}]" for c in allowed}
    definition = text.strip()
    if definition == "INSUFFICIENT":
        return "", [], []
    found = [m.group(0) for m in _MARKER_RE.finditer(definition)]
    cited = [m for m in dict.fromkeys(found) if m in known]
    unknown = [m for m in dict.fromkeys(found) if m not in known]
    return definition, cited, unknown


def select(term: str, chunks: Iterable[Chunk], *, top: int = 8) -> list[Chunk]:
    return shortlist(chunks, term, top=top)


def finish(
    term: str, reply_text: str, selected: list[Chunk], documents: Iterable[Document]
) -> Entry:
    documents = list(documents)
    if not selected:
        return Entry(term=term, definition="", citations=())

    definition, cited, unknown = parse_definition(reply_text, selected)
    by_marker = {f"[{c.doc_id}:{c.ordinal}]": c for c in selected}
    if definition:
        citations = tuple(cite([by_marker[m] for m in cited], documents))
    else:
        # Honest degrade (NoopEngine, or excerpts that don't define the term):
        # no definition, but still hand back the best excerpt so the reader can
        # judge for themselves. Never a fabricated sentence.
        citations = tuple(cite(selected[:1], documents))

    return Entry(
        term=term, definition=definition, citations=citations, unknown_markers=tuple(unknown)
    )


def define(
    term: str,
    documents: Iterable[Document],
    chunks: Iterable[Chunk],
    engine: Engine,
    *,
    top: int = 8,
) -> tuple[Entry, list[Chunk]]:
    documents = list(documents)
    selected = select(term, chunks, top=top)
    if not selected:
        return Entry(term=term, definition="", citations=()), []
    reply = engine.run(EngineRequest(prompt=build_prompt(term, selected, documents), system=SYSTEM))
    return finish(term, reply.text, selected, documents), selected


def slug(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-") or "term"


def render_markdown(entry: Entry, chunks: Iterable[Chunk]) -> str:
    by_marker = {f"[{c.doc_id}:{c.ordinal}]": c for c in chunks}
    lines = [f"# {entry.term}", ""]
    if entry.definition:
        lines += [entry.definition, ""]
    else:
        lines += ["_No definition: the corpus excerpts did not define this term._", ""]
    lines += ["## Sources", ""]
    for c in entry.citations:
        excerpt = by_marker.get(c.marker())
        quote = " ".join(excerpt.text.split())[:200] if excerpt else ""
        lines.append(f"- `{c.marker()}` **{c.title}** (chars {c.start}–{c.end})")
        if quote:
            lines.append(f"  > {quote}…")
    return "\n".join(lines) + "\n"
