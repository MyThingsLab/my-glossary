from __future__ import annotations

from pathlib import Path

import pytest
from mythings.engine import ClaudeCLIEngine, EngineResult, NoopEngine

from myglossary.cli import main
from myglossary.tool import Result


class SpyTool:
    instances: list[SpyTool] = []
    result = Result(outcome="noop", detail="nothing to change for #1", issue=1)

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        SpyTool.instances.append(self)

    def run(self, issue_number: int | None = None) -> Result:
        self.issue_number = issue_number
        return self.result


@pytest.fixture(autouse=True)
def _reset_spy() -> None:
    SpyTool.instances = []
    SpyTool.result = Result(outcome="noop", detail="nothing to change for #1", issue=1)


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    (tmp_path / "notes.md").write_text(
        "Overfitting is when a model memorises the training set.\n\n"
        "Cross validation estimates generalisation error by holding out folds.\n"
    )
    return tmp_path


def test_build_wires_the_tool_and_reports(corpus: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "build",
            "--issue",
            "3",
            "--repo",
            "o/r",
            "--label",
            "my-x",
            "--base",
            "dev",
            "--corpus",
            str(corpus),
        ],
        tool_factory=SpyTool,
    )
    assert rc == 0
    (tool,) = SpyTool.instances
    assert tool.issue_number == 3
    assert tool.kwargs["label"] == "my-x"
    assert tool.kwargs["base"] == "dev"
    assert tool.kwargs["github"].repo == "o/r"
    assert tool.kwargs["corpus"] == [corpus]
    assert isinstance(tool.kwargs["engine"], NoopEngine)
    assert tool.kwargs["ledger"].path == Path(".mythings/ledger.jsonl")
    assert capsys.readouterr().out.strip() == "noop: nothing to change for #1 (issue #1)"


def test_engine_flag_selects_the_claude_backend(corpus: Path) -> None:
    main(["build", "--engine", "claude", "--corpus", str(corpus)], tool_factory=SpyTool)
    (tool,) = SpyTool.instances
    assert isinstance(tool.kwargs["engine"], ClaudeCLIEngine)


def test_denied_build_exits_nonzero(corpus: Path) -> None:
    SpyTool.result = Result(outcome="denied", detail="policy blocked it", issue=1)
    assert main(["build", "--corpus", str(corpus)], tool_factory=SpyTool) == 1


def test_define_prints_the_definition_and_its_sources(
    corpus: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Fake:
        def run(self, request):
            return EngineResult(text="Memorising the training set [notes:0].")

    monkeypatch.setattr("myglossary.cli._engine", lambda name: Fake())
    rc = main(["define", "overfitting", "--corpus", str(corpus)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "Memorising the training set [notes:0]." in out
    assert "[notes:0]" in out.split("Sources:")[1]


def test_define_degrades_honestly_under_the_noop_engine(
    corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["define", "overfitting", "--corpus", str(corpus)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no definition" in out
    # Still hands back the best excerpt so the reader can judge for themselves.
    assert "Sources:" in out


def test_define_warns_loudly_about_a_fabricated_citation(
    corpus: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Liar:
        def run(self, request):
            return EngineResult(text="Overfitting is bad [bishop-prml:99].")

    monkeypatch.setattr("myglossary.cli._engine", lambda name: Liar())
    main(["define", "overfitting", "--corpus", str(corpus)])
    out = capsys.readouterr().out
    assert "fabricated citations ignored: [bishop-prml:99]" in out


def test_define_reports_an_empty_corpus(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["define", "x", "--corpus", str(tmp_path)]) == 1
    assert "no corpus files found" in capsys.readouterr().out


def test_define_cache_flag_populates_and_reuses_the_cache_dir(
    corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = corpus / "cache"
    for _ in range(2):
        assert (
            main(
                [
                    "define",
                    "overfitting",
                    "--corpus",
                    str(corpus / "notes.md"),
                    "--cache",
                    str(cache),
                ]
            )
            == 0
        )
    # One source file -> exactly one cache entry, still there after the second run.
    assert [p.suffix for p in cache.iterdir()] == [".txt"]


def test_build_forwards_the_cache_dir_to_the_tool(corpus: Path) -> None:
    cache = corpus / "cache"
    main(["build", "--corpus", str(corpus), "--cache", str(cache)], tool_factory=SpyTool)
    (tool,) = SpyTool.instances
    assert tool.kwargs["cache"] == cache


def test_define_without_cache_touches_no_cache_dir(corpus: Path) -> None:
    assert main(["define", "overfitting", "--corpus", str(corpus / "notes.md")]) == 0
    assert not (corpus / "cache").exists()


def test_an_unknown_command_is_rejected() -> None:
    with pytest.raises(SystemExit):
        main(["frobnicate"])
