# DocuBench open-source polish plan

## Objective

Rename and extend this repository into **DocuBench**, a credible public benchmark for schema-guided structured document extraction. The repo should read as neutral benchmark infrastructure first, with product results presented as baseline submissions rather than as the primary identity.

## Observations

- The repo already has the hard benchmark assets: 50 source documents, 50 JSON schemas, 50 hand-verified labels, a provenance manifest, raw extraction outputs, and an array-aware scorer.
- The current README still reads like a head-to-head vendor comparison. High-polish benchmark repos usually lead with the task contract, dataset composition, scoring methodology, reproducibility, and contribution path.
- The current code is script-shaped rather than package-shaped. A serious benchmark should offer an installable CLI, predictable commands, validation, tests, and CI.
- `python scripts/score_all.py` is documented, but this machine only exposes `python3`; polished setup should avoid PATH ambiguity through package scripts or explicit `python3` commands.
- Scoring is useful but under-documented. Users need a formal scoring spec covering normalization, array matching, blank handling, aggregate weighting, and known limitations.
- Results should be framed as submissions/baselines with metadata. This makes room for future systems without coupling the benchmark identity to any vendor.
- The repo has a strong `SOURCES.md`, but it needs a dataset card, limitations, submission policy, and citation metadata to feel like a reusable research artifact.
- There is no test suite or continuous validation gate. For a benchmark, schema/label/result integrity should be checked automatically.

## Execution checklist

1. Add a package layer and CLI:
   - `pyproject.toml`
   - `docubench` package
   - `docubench validate`
   - `docubench score`
   - `docubench report`

2. Reframe repository documentation:
   - Rewrite README around DocuBench as a neutral benchmark.
   - Keep published baselines, but present them as baseline submissions.
   - Use runnable commands that work with the package and with `python3`.

3. Add benchmark documentation:
   - scoring methodology
   - dataset card
   - limitations
   - submission format

4. Add open-source project hygiene:
   - `CONTRIBUTING.md`
   - `CITATION.cff`
   - CI workflow
   - tests for scorer behavior and CLI validation

5. Verify:
   - validate the benchmark files
   - reproduce aggregate scores
   - run tests

