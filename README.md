# my-glossary

[![CI](https://github.com/MyThingsLab/my-glossary/actions/workflows/ci.yml/badge.svg)](https://github.com/MyThingsLab/my-glossary/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/MyThingsLab/my-glossary/branch/main/graph/badge.svg)](https://codecov.io/gh/MyThingsLab/my-glossary) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Defines a term from a document corpus — PDFs, lecture notes, textbooks — and
cites the exact source spans the definition came from.

MyGlossary is the first consumer of the **`mythings.corpus`** seam
([ADR 0001](../my-things-core/docs/adr/0001-corpus-seam.md)), built deliberately
small to prove that contract before MySyllabus, MyProfessor, MyFlashcards and
MyGrader depend on it.

## The shape

Retrieval is **deterministic and happens before the model runs**:
`mythings.corpus` ingests the corpus, chunks it, and shortlists excerpts by
token overlap. The Engine is then asked one question — *define this term using
only these excerpts* — and every citation it returns is **validated against the
excerpts it was actually shown**. A marker naming an excerpt the model never
saw is a fabrication: it is stripped from the entry and reported loudly.

The model never chooses its own sources, and it is never the thing that decides
whether a citation is real.

## Usage

Two paths, because cramming for an exam and running unattended in a fleet want
different ceremony.

**Read locally** — no issue, no PR, no CI:

```bash
myglossary define "EM algorithm" --corpus ~/Desktop/unsupervised_learning.pdf --engine claude
```

```
EM algorithm
============

The EM (Expectation-Maximization) algorithm is an algorithm for estimating maximum
likelihood parameters of a model with latent variables [unsupervised-learning:31] ...

Sources:
  [unsupervised-learning:31] unsupervised_learning (chars 25136-25982)
  [unsupervised-learning:32] unsupervised_learning (chars 25984-27184)
```

**Write through a PR** — one labeled issue becomes one glossary entry:

```bash
myglossary build --issue 12 --corpus ~/Desktop/books --repo MyThingsLab/study
```

`--engine noop` is a zero-token dry run everywhere: it emits no definition and
hands back the top-scoring excerpt instead, never a fabricated sentence.

## Invariants

- Exactly **one Engine call** per invocation.
- Retrieval is deterministic and pre-Engine; citations are validated post-Engine.
- `define` is read-only. Only `build` mutates, as one `Action` through
  `Policy.evaluate()`, opening a draft PR.
- A term the corpus does not define is a **no-op**, not a stub entry.
- Never ingests a corpus into the repo — it reads source material in place.

## Requirements

PDF extraction shells out to `pdftotext` (`poppler-utils`). Callers with other
formats inject their own `Extractor`; core stays dependency-free.

## In the fleet loop

MyUni decomposes a field into topic issues; MyResearcher briefs them. MyGlossary
defines the terms those briefs assume you already know.
