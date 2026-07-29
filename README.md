# DocuBench

**A public benchmark for schema-guided structured extraction from 72 hard, real-world documents.**

Built and maintained by **[DocuPipe](https://www.docupipe.ai)**. Every system, including DocuPipe, is scored by the same open scorer against the same hand-verified labels. 

[![CI](https://github.com/DocuPipe/docubench/actions/workflows/ci.yml/badge.svg)](https://github.com/DocuPipe/docubench/actions/workflows/ci.yml)
[![Code: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![Source documents: mixed terms](https://img.shields.io/badge/source%20documents-mixed%20terms-blue.svg)](docs/dataset-card.md)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

DocuBench is built to break extraction systems on what real documents actually look like: multi-row arrays and multi-page tables, totals that must reconcile, right-to-left and CJK scripts, rotated scans, handwriting, and ten different file types.

<br>

<a href="https://htmlpreview.github.io/?https://github.com/DocuPipe/docubench/blob/main/docubench-explorer.html">
  <img src="docs/explorer-preview.png" alt="DocuBench interactive results explorer — filter 72 documents and compare nine complete configurations" width="100%">
</a>

<h3 align="center"><a href="https://htmlpreview.github.io/?https://github.com/DocuPipe/docubench/blob/main/docubench-explorer.html">🔎&nbsp; Open the interactive results explorer &nbsp;→</a></h3>
<p align="center">
  Filter all 72 documents by language, length, format &amp; capability · compare nine complete configurations · drill into per-document scores.<br>
</p>

<br>

**Explore:** [Leaderboard](#leaderboard) · [Results explorer](https://htmlpreview.github.io/?https://github.com/DocuPipe/docubench/blob/main/docubench-explorer.html) · [Hosted leaderboard](https://huggingface.co/spaces/DocuPipe/docubench-leaderboard)  
**Docs:** [Dataset card](docs/dataset-card.md) · [Scoring](docs/scoring.md) · [Make a submission](#make-a-submission)

---

## Leaderboard

The complete committed baselines, scored by the public scorer ([`scorer.py`](scorer.py)) against the hand-verified labels. Headline metric is **macro-average field accuracy** with order-independent array matching. Every ranked system covers all 72 documents. Alongside the specialized extraction platforms, we include three frontier LLMs called **directly** with the same schema and a generous output budget (each at its own maximum) — the do-it-yourself baseline a team would build in-house.

| Rank | System | Accuracy |
|---:|---|---:|
| 🥇 | **DocuPipe** — high effort | **97.02%** |
| 🥈 | **DocuPipe** — standard effort | **96.14%** |
| 🥉 | Claude Sonnet 5 — direct LLM | 91.73% |
| 4 | Reducto — Deep Extract | 89.38% |
| 5 | Reducto — standard | 81.11% |
| 6 | Extend | 80.28% |
| 7 | GPT-5.5 — direct LLM | 76.48% |
| 8 | Gemini 3.5 Flash — direct LLM | 72.98% |
| 9 | Unstructured | 67.67% |

> DocuPipe built this benchmark, so we hold our own results to the same bar as everyone else: identical schemas, identical labels, the same open scorer, and every raw model output committed under [`results/`](results). Run `docubench score` and you will reproduce this table.

**🔎 Explore it interactively.** Open the [**results explorer**](https://htmlpreview.github.io/?https://github.com/DocuPipe/docubench/blob/main/docubench-explorer.html) to filter all 72 documents by file type, language, and capability and drill into per-document scores. It is a single self-contained file ([`docubench-explorer.html`](docubench-explorer.html)) you can also open locally. A hosted [Hugging Face Space](https://huggingface.co/spaces/DocuPipe/docubench-leaderboard) renders the same leaderboard online, and full per-document numbers live in [`results/summary.json`](results/summary.json).

These are baseline submissions, not a closed leaderboard — the repository is structured so any new system can be scored against the same documents.

## Why DocuBench is hard

Each of the 72 documents was chosen for a specific failure mode that trips up real extraction systems:

- **Arrays & line-item tables** (51 docs) — invoices, statements, directories, and reference works where rows must be extracted as structured arrays.
- **Reconciling totals** (26 docs) — sums, subtotals, counts, and grand totals that must add up.
- **Multi-page context** — transactions and tables that straddle page breaks.
- **Right-to-left scripts** (8 docs) — Hebrew and Arabic invoices, payslips, financials, and a scanned lexicon.
- **CJK scripts** (6 docs) — Japanese and Chinese invoices, receipts, government records, and directories.
- **Rotated scans** (3 docs) and **handwriting** (3 docs) — robustness to messy capture.
- **Nested objects & needle-in-haystack lookups** — deep structures and single records buried in large exports.
- **Ten file types** beyond PDF — JPEG, PNG, TIFF, XLSX, CSV, XML, TXT, DOCX, HTML.

## What's in the benchmark

| | |
|---|---|
| **Documents** | 72 reproducibly sourced files |
| **File types** | 10 — PDF, JPEG, PNG, TIFF, XLSX, CSV, XML, TXT, DOCX, HTML |
| **Languages / scripts** | 12 — English, Hebrew, Japanese, Chinese, Arabic, French, German, Portuguese, Dutch, Italian, Spanish, Hindi/Devanagari |
| **Per task** | source document · JSON Schema · hand-verified JSON label |
| **Metric** | macro-average field accuracy with order-independent array matching |
| **Also included** | raw baseline outputs, the scorer, source manifest, committed prompts |

See the [dataset card](docs/dataset-card.md) for composition and intended use, and [limitations](docs/limitations.md) for what the benchmark does *not* measure.

## How a task works

A system receives a **source document** and its **JSON Schema**, and must return JSON matching the schema. The output is scored field-by-field against the hand-verified label.

```jsonc
// schemas/<doc_id>.json  (abridged)
{
  "type": "object",
  "properties": {
    "invoiceNumber": { "type": "string" },
    "lineItems": {
      "type": "array",
      "items": { "type": "object", "properties": {
        "description": { "type": "string" },
        "quantity":    { "type": "number" },
        "total":       { "type": "number" }
      }}
    },
    "grandTotal": { "type": "number" }
  }
}
```

```jsonc
// labels/<doc_id>.json  (the hand-verified target)
{
  "invoiceNumber": "INV-10001",
  "lineItems": [
    { "description": "Steel beams", "quantity": 5,  "total": 1000.0 },
    { "description": "Labor",       "quantity": 12, "total": 1440.0 }
  ],
  "grandTotal": 2440.0
}
```

Strings are normalized for benign whitespace/punctuation/case, numbers are compared as floats, and `lineItems` is matched **order-independently** — returning the same rows in a different order is not penalized.

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"

docubench validate   # check document/schema/label/result integrity
docubench score      # reproduce the committed scores
docubench report     # regenerate results/summary.json and summary.csv
```

The standalone scorer is stdlib-only and needs no install:

```bash
python3 scorer.py results/gpt/PSU5pciM.json schemas/PSU5pciM.json labels/PSU5pciM.json
```

## Scoring

[`scorer.py`](scorer.py) performs field-level scoring against the schema-shaped label:

- strings are normalized for whitespace, punctuation, and case
- numbers are cast to float and rounded
- arrays are matched order-independently with greedy best-pair assignment
- both-blank fields are skipped; one-side-blank counts as a miss
- the document score is a leaf-weighted average; the headline number is the macro average across documents

The full contract — normalization, array matching, blank handling, and known trade-offs — is in [`docs/scoring.md`](docs/scoring.md). Scoring changes are treated as benchmark-version changes.

## Make a submission

For each document, run your system with the paired schema and write `results/<system_name>/<doc_id>.json`:

```json
{
  "data": { "invoiceNumber": "INV-10001", "lineItems": [] },
  "meta": { "model": "your-model-or-version" }
}
```

Then score and open a pull request:

```bash
docubench validate
docubench score --engine <system_name>
```

See [`docs/submissions.md`](docs/submissions.md) for the recommended metadata and review expectations.

## Reproduce the baselines

The model runners send each document, its paired schema, and any `guidelines/<doc_id>.txt` instructions to a provider and write the result envelope to `results/<engine>/<doc_id>.json`. Failures (API/model/schema) are written with `status: "failed"` and `data: {}`, so the scorer counts every labeled field as a miss instead of silently dropping the document. TIFF inputs are converted to ordered PNG pages for providers that do not accept TIFF.

| Engine | Script | Credentials |
|---|---|---|
| GPT-5.5 | `scripts/run_gpt.py` | `OPENAI_API_KEY` |
| Claude Sonnet 5 | `scripts/run_claude_bedrock.py` | AWS Bedrock credentials |
| Gemini 3.5 Flash | `scripts/run_gemini.py` | `GOOGLE_API_KEY` |
| Extend | `scripts/run_extend.py` | `EXTEND_API_KEY` |
| Reducto | `scripts/run_reducto.py` | `REDUCTO_API_KEY` |
| Unstructured | `scripts/run_unstructured.py` | `UNSTRUCTURED_API_KEY` |

The three direct-LLM runners give each model its full output budget — Claude Sonnet 5 and GPT-5.5 up to 128K tokens, Gemini 3.5 Flash up to 65,536 — so a low output cap never causes an artificial failure. Override per-model with `OPENAI_MAX_OUTPUT_TOKENS`, `BEDROCK_CLAUDE_MAX_TOKENS`, or `GEMINI_MAX_OUTPUT_TOKENS`.

```bash
export OPENAI_API_KEY=...
python3 scripts/run_gpt.py documents/PSU5pciM.pdf schemas/PSU5pciM.json results/gpt/PSU5pciM.json

# or run the full benchmark idempotently (skips completed docs; --force to rerun)
python3 scripts/run_all.py --engine gpt
python3 scripts/run_all.py --engine claude5
python3 scripts/run_all.py --engine gemini

# reducto ships two modes; the output dir selects deep vs standard extract
python3 scripts/run_all.py --engine reducto
python3 scripts/run_all.py --engine reducto_standard
```

Default models can be overridden via `OPENAI_MODEL`, `ANTHROPIC_MODEL`, `GEMINI_MODEL`. The exact instruction prompt and per-system configuration are committed in [`prompts/`](prompts) — the LLM runners load [`prompts/extraction_prompt.txt`](prompts/extraction_prompt.txt) at runtime, so the committed prompt is provably the one that produced the baseline results.

## Repository layout

```text
documents/<doc_id>.<ext>          source documents
schemas/<doc_id>.json             extraction schemas
guidelines/<doc_id>.txt           optional schema-level extraction instructions
labels/<doc_id>.json              hand-verified labels
results/<system>/<doc_id>.json    baseline system outputs
results/summary.{json,csv}        aggregate and per-document scores
sources.json / SOURCES.md         source manifests (machine + human readable)
scorer.py                         standalone scorer, stdlib only
docubench/                       installable CLI (validate / score / report)
prompts/                          committed prompts and run config per baseline
space/                            Hugging Face Space leaderboard
docubench-explorer.html          self-contained interactive results explorer
docs/                             scoring, dataset card, submissions, limitations
tests/                            scorer, CLI, prompt, and Space tests
```

## Provenance and licensing

- **Code:** MIT — see [`LICENSE`](LICENSE).
- **Labels, schemas, and benchmark-authored metadata/results:** CC BY 4.0 unless a file states otherwise.
- **Documents:** each source retains its original license or publication basis — see [`SOURCES.md`](SOURCES.md) and [`sources.json`](sources.json).

If you are a rights-holder and want a document removed, open an issue with the document ID and source details.

## Citation

If you use DocuBench in research or public comparisons, please cite this repository. A machine-readable record is in [`CITATION.cff`](CITATION.cff).

## Built by DocuPipe

DocuBench is built and maintained by [DocuPipe](https://www.docupipe.ai). The release write-up walks through the benchmark and how systems compare on it: [DocuPipe on 72 hard, real-world documents](https://www.docupipe.ai/blog/docupipe-vs-extend-benchmark). Contributions and new system submissions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).
