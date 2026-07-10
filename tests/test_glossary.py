from __future__ import annotations

from pathlib import Path

import pytest
from mythings.corpus import Chunk, Document, extract
from mythings.engine import EngineResult, NoopEngine

from myglossary.glossary import (
    corpus_files,
    define,
    finish,
    format_excerpts,
    load_corpus,
    parse_definition,
    render_markdown,
    resolve_extractor,
    select,
    slug,
)


def _chunk(doc_id: str = "d", ordinal: int = 0, text: str = "body") -> Chunk:
    return Chunk(doc_id=doc_id, ordinal=ordinal, text=text, start=0, end=len(text))


def _doc(doc_id: str = "d", title: str = "Doc", text: str = "body") -> Document:
    return Document(id=doc_id, path=f"/{doc_id}.md", title=title, text=text)


class ScriptedEngine:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def run(self, request) -> EngineResult:
        self.calls += 1
        self.request = request
        return EngineResult(text=self.reply)


def test_corpus_files_picks_up_pdfs_and_text_and_ignores_the_rest(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "b.pdf").write_bytes(b"%PDF")
    (tmp_path / "c.png").write_bytes(b"\x89PNG")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.txt").write_text("y")

    assert [p.name for p in corpus_files([tmp_path])] == ["a.md", "b.pdf", "d.txt"]


def test_resolve_extractor_none_is_the_plain_extractor() -> None:
    assert resolve_extractor(None) is extract


def test_resolve_extractor_with_a_dir_caches(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("body")
    ex = resolve_extractor(tmp_path / "cache")
    assert ex(src) == "body"
    assert (tmp_path / "cache").is_dir()


def test_load_corpus_passes_the_extractor_through(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("ignored on disk")
    docs, _ = load_corpus([tmp_path], extractor=lambda p: "injected")
    assert docs[0].text == "injected"


def test_load_corpus_chunks_every_document(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("para one\n\npara two")
    documents, chunks = load_corpus([tmp_path], target_chars=8)
    assert [d.id for d in documents] == ["notes"]
    assert [c.text for c in chunks] == ["para one", "para two"]


def test_format_excerpts_labels_each_with_its_citation_marker() -> None:
    text = format_excerpts([_chunk(ordinal=3, text="the  body")], [_doc()])
    assert text.startswith("[d:3] (Doc)")
    assert "the body" in text  # whitespace collapsed


def test_parse_definition_separates_known_from_fabricated_markers() -> None:
    selected = [_chunk(ordinal=0), _chunk(ordinal=1)]
    definition, cited, unknown = parse_definition(
        "Real [d:0] and also [d:1], plus a lie [ghost:9].", selected
    )
    assert cited == ["[d:0]", "[d:1]"]
    assert unknown == ["[ghost:9]"]
    # The prose is returned untouched; only the citation list is filtered.
    assert "[ghost:9]" in definition


def test_parse_definition_treats_insufficient_as_no_definition() -> None:
    assert parse_definition("INSUFFICIENT", [_chunk()]) == ("", [], [])


def test_finish_drops_a_fabricated_citation_from_the_entry() -> None:
    selected = [_chunk(ordinal=0)]
    entry = finish("term", "Claim [d:0] and lie [d:7].", selected, [_doc()])
    assert [c.marker() for c in entry.citations] == ["[d:0]"]
    assert entry.unknown_markers == ("[d:7]",)


def test_finish_degrades_to_the_top_excerpt_when_there_is_no_definition() -> None:
    selected = [_chunk(ordinal=2, text="best"), _chunk(ordinal=5, text="worse")]
    entry = finish("term", "", selected, [_doc()])
    assert entry.is_degraded()
    assert entry.definition == ""
    assert [c.marker() for c in entry.citations] == ["[d:2]"]


def test_define_makes_exactly_one_engine_call() -> None:
    engine = ScriptedEngine("A definition [d:0].")
    doc = _doc(text="body")
    entry, selected = define("term", [doc], [_chunk()], engine)
    assert engine.calls == 1
    assert entry.definition == "A definition [d:0]."
    assert len(selected) == 1


def test_define_on_an_empty_corpus_makes_no_engine_call() -> None:
    engine = ScriptedEngine("should not be called")
    entry, selected = define("term", [], [], engine)
    assert engine.calls == 0
    assert entry.is_degraded()
    assert selected == []


def test_define_shows_the_engine_only_the_shortlisted_excerpts() -> None:
    engine = ScriptedEngine("ok")
    doc = _doc()
    hit = _chunk(ordinal=0, text="the EM algorithm maximises likelihood")
    miss = _chunk(ordinal=1, text="kittens are unrelated")
    define("EM algorithm", [doc], [miss, hit], engine, top=1)
    assert "EM algorithm maximises" in engine.request.prompt
    assert "kittens" not in engine.request.prompt


def test_noop_engine_never_fabricates_a_definition() -> None:
    entry, _ = define("term", [_doc()], [_chunk()], NoopEngine())
    assert entry.definition == ""
    assert entry.is_degraded()


def test_select_is_deterministic_token_overlap() -> None:
    hit = _chunk(ordinal=1, text="principal component analysis")
    miss = _chunk(ordinal=0, text="unrelated prose")
    assert select("principal component", [miss, hit], top=1) == [hit]


def test_render_markdown_quotes_each_cited_span() -> None:
    selected = [_chunk(ordinal=0, text="the source sentence")]
    entry = finish("Overfitting", "Def [d:0].", selected, [_doc(title="Notes")])
    md = render_markdown(entry, selected)
    assert md.startswith("# Overfitting")
    assert "Def [d:0]." in md
    assert "`[d:0]` **Notes**" in md
    assert "> the source sentence" in md


def test_render_markdown_is_explicit_when_undefined() -> None:
    selected = [_chunk()]
    md = render_markdown(finish("t", "", selected, [_doc()]), selected)
    assert "_No definition" in md


@pytest.mark.parametrize(
    ("term", "expected"),
    [("EM Algorithm", "em-algorithm"), ("k-means++", "k-means"), ("!!!", "term")],
)
def test_slug_is_filesystem_safe(term: str, expected: str) -> None:
    assert slug(term) == expected
