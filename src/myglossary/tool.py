from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from mythings.corpus import Chunk, Document
from mythings.engine import Engine, EngineRequest, EngineResult
from mythings.github import GitHub, Issue
from mythings.isolation import Workspace, in_github_actions
from mythings.ledger import Ledger
from mythings.policy import ALLOW, Action, Decision, Policy, PolicyResult

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


class _AllowAll:
    # Default Policy for tools whose one side effect is a non-destructive draft
    # PR (my-changelogger's rationale). Swap in a real gate (e.g. myguard.Guard)
    # when the tool's `apply` grows teeth.
    def evaluate(self, action: Action) -> PolicyResult:
        return ALLOW


def _run_git(tree: Path, argv: list[str]) -> None:
    proc = subprocess.run(["git", *argv], cwd=tree, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(argv)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )


@dataclass(frozen=True)
class Result:
    outcome: str  # success | noop | skipped | denied | failure
    detail: str
    issue: int | None = None
    pr: int | None = None


class Tool:
    # The harness loop, already wired: read one labeled issue → deterministic
    # pre-work → one Engine call → apply inside an isolated Workspace → draft
    # PR, with the side effect gated by Policy and every run ledgered. A new
    # tool overrides the three seam methods (prework/request/apply) and the
    # constants above; the plumbing here should not need to change.

    def __init__(
        self,
        *,
        repo: str | Path = ".",
        ledger: Ledger,
        github: GitHub,
        engine: Engine,
        policy: Policy | None = None,
        base: str = "main",
        label: str = BACKLOG_LABEL,
        git: Callable[[Path, list[str]], None] = _run_git,
        corpus: Sequence[Path] = (),
        top: int = 8,
        entries_dir: str = "glossary",
        cache: Path | None = None,
    ) -> None:
        self.repo = Path(repo)
        self.ledger = ledger
        self.github = github
        self.engine = engine
        self.policy = policy or _AllowAll()
        self.base = base
        self.label = label
        self._git = git
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

    # -- plumbing --------------------------------------------------------

    def pick_issue(self, number: int | None = None) -> Issue | None:
        issues = self.github.list_issues(labels=[self.label])
        if number is not None:
            return next((i for i in issues if i.number == number), None)
        return min(issues, key=lambda i: i.number) if issues else None

    def run(self, issue_number: int | None = None) -> Result:
        issue = self.pick_issue(issue_number)
        if issue is None:
            detail = f"no open '{self.label}' issue" + (
                f" #{issue_number}" if issue_number is not None else ""
            )
            self.ledger.record(TOOL, LEDGER_KIND, "skipped", detail)
            return Result(outcome="skipped", detail=detail)

        context = self.prework(issue)
        engine_result = self.engine.run(self.request(issue, context))

        with Workspace(self.repo, base_ref=f"origin/{self.base}") as tree:
            relpath = self.apply(tree, issue, engine_result)
            if relpath is None:
                detail = f"nothing to change for #{issue.number}"
                self.ledger.record(TOOL, LEDGER_KIND, "noop", detail, issue=issue.number)
                return Result(outcome="noop", detail=detail, issue=issue.number)

            branch = f"{self.label}/{issue.number}"
            gate = self.policy.evaluate(
                Action(kind="bash", payload={"command": f"gh pr create --head {branch}"})
            )
            if gate.under(unattended=in_github_actions()) is not Decision.ALLOW:
                detail = f"policy blocked the PR for #{issue.number}: {gate.reason or gate.rule}"
                self.ledger.record(TOOL, LEDGER_KIND, "denied", detail, issue=issue.number)
                return Result(outcome="denied", detail=detail, issue=issue.number)

            self._git(tree, ["checkout", "-b", branch])
            self._git(tree, ["add", relpath])
            self._git(tree, ["commit", "-m", f"{TOOL}: {issue.title}"])
            self._git(tree, ["push", "-u", "origin", branch])
            pr = self.github.open_pr(
                title=issue.title,
                body=f"Closes #{issue.number}.",
                base=self.base,
                head=branch,
                draft=True,
            )

        self.ledger.record(
            TOOL,
            LEDGER_KIND,
            "success",
            f"PR #{pr.number} for #{issue.number}",
            issue=issue.number,
            pr=pr.number,
        )
        return Result(
            outcome="success",
            detail=f"opened draft PR #{pr.number}",
            issue=issue.number,
            pr=pr.number,
        )
