# my-glossary — agent instructions

You are developing **my-glossary**, a MyThingsLab My[X] tool.

**Inherited rules:** obey [`./HARNESS.md`](./HARNESS.md) in full — the vendored
MyThingsLab build-harness rules. Do not restate or override them. Anything not
covered here defers to `HARNESS.md`, then `my-things-core/docs/CONVENTIONS.md`.

## This tool

- **Purpose:** defines a term from a document corpus (PDFs, notes, slides) and
  cites the exact source spans the definition came from. The first consumer of
  the `mythings.corpus` seam (ADR 0001), built to prove that contract before
  MySyllabus, MyProfessor, MyFlashcards and MyGrader depend on it.
- **The single Engine call:** one per invocation — "define this term using only
  these excerpts, citing each claim inline with a `[doc-id:ordinal]` marker
  copied verbatim from an excerpt heading" → definition prose. Against
  `NoopEngine`, emits no definition and hands back the top-scoring excerpt
  instead — an honest degrade, never a fabricated sentence.
- **Invariants / rules:** exactly one Engine call per run. Retrieval is
  **deterministic and pre-Engine** — `mythings.corpus.shortlist()` picks the
  excerpts by token overlap; the model never chooses its own sources. Every
  citation is **validated post-Engine** against the excerpts actually shown:
  a marker naming an excerpt the model was never given is a fabrication, and is
  stripped from the entry and reported loudly, never silently kept. The `define`
  read path is local-only — stdout, no issue, no PR, no CI. Only `build` mutates,
  as one `Action(kind="bash")` through `Policy.evaluate()`, opening a draft PR.
  A term the corpus does not define ends the run as a **no-op**, not a stub entry.
  Never ingests a corpus into the repo; it reads source material in place.
- **Backlog label:** `my-glossary`
