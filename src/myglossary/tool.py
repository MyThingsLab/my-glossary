from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from mythings.corpus import Chunk, Document
from mythings.engine import Engine, EngineRequest, EngineResult
from mythings.github import GitHub, Issue
from mythings.ledger import Ledger
from mythings.policy import Policy
from mythings.tool import BaseToolRunner
from mythings.tool import ToolRunResult as Result

from myglossary.glossary import (
    SYSTEM,
    build_prompt,
    finish,
    format_excerpts,
    load_corpus,
    render_markdown,
    resolve_extractor,
    select,
    slug,
)

# The per-tool constants. The rename step (scripts/init.py, or the manual grep
# sweep in README.md) rewrites these alongside the CLAUDE.md seams.
TOOL = "myglossary"
LEDGER_KIND = "glossary"  # this tool's own runtime-Ledger kind
BACKLOG_LABEL = "my-glossary"  # the GitHub issue label it picks up


class Tool(BaseToolRunner):
    # The harness loop, already wired: read one labeled issue → deterministic
    # pre-work → one Engine call → apply inside an isolated Workspace → draft
    # PR, with the side effect gated by Policy and every run ledgered. A new
    # tool overrides the three seam methods (prework/request/apply) and the
    # constants above; the plumbing here should not need to change.

    def __init__(
        self,
        *,
        repo: str | Path = ".",
        ledger: Ledger | None = None,
        github: GitHub | None = None,
        engine: Engine | None = None,
        policy: Policy | None = None,
        base: str = "main",
        label: str = BACKLOG_LABEL,
        git: Callable[[Path, list[str]], None] | None = None,
        corpus: Sequence[Path] = (),
        top: int = 8,
        entries_dir: str = "glossary",
        cache: Path | None = None,
    ) -> None:
        super().__init__(
            repo=repo,
            ledger=ledger,
            github=github,
            engine=engine,
            policy=policy,
            base=base,
            label=label,
            git=git,
        )
        self.corpus = list(corpus)
        self.top = top
        self.entries_dir = entries_dir
        self._extractor = resolve_extractor(cache)
        # prework() selects the excerpts; apply() must cite exactly those and no
        # others. The template's seam signatures carry only the Engine reply
        # between them, so the selection is held here across the one call.
        self._selected: list[Chunk] = []
        self._documents: list[Document] = []

    # -- seams -----------------------------------------------------------

    def prework(self, issue: Issue) -> str:
        # Deterministic pre-work: load the corpus and shortlist the excerpts
        # for the term. No judgment here -- token overlap, not a model.
        self._documents, chunks = load_corpus(self.corpus, extractor=self._extractor)
        self._selected = select(issue.title, chunks, top=self.top)
        return format_excerpts(self._selected, self._documents)

    def request(self, issue: Issue, context: str) -> EngineRequest:
        # The single Engine call: define the issue's term from those excerpts.
        return EngineRequest(
            prompt=build_prompt(issue.title, self._selected, self._documents), system=SYSTEM
        )

    def apply(self, tree: Path, issue: Issue, result: EngineResult) -> str | None:
        if not self._selected:
            return None
        entry = finish(issue.title, result.text, self._selected, self._documents)
        if entry.is_degraded():
            # The corpus does not define the term. Writing a stub entry that says
            # so would be noise in the study repo -- end as a no-op instead.
            return None
        relpath = f"{self.entries_dir}/{slug(entry.term)}.md"
        target = tree / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(entry, self._selected), encoding="utf-8")
        return relpath

    def run(self, issue_number: int | None = None) -> Result:
        return self.run_issue_workflow(TOOL, LEDGER_KIND, issue_number)

