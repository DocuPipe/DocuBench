# Submission Format

DocuBench result sets live under:

```text
results/<system_name>/<doc_id>.json
```

`<system_name>` should be lowercase, filesystem-safe, and stable across reports.

## Required Per-Document File

Each document result should be a JSON object with a top-level `data` key:

```json
{
  "data": {
    "invoice_number": "INV-001",
    "line_items": []
  }
}
```

The `data` object is scored against `labels/<doc_id>.json` using `schemas/<doc_id>.json`.

## Recommended Metadata

Include run metadata when available:

```json
{
  "data": {},
  "cost": 0.0,
  "time_sec": 0.0,
  "meta": {
    "system": "example-system",
    "model": "model-or-api-version",
    "run_date": "2026-06-26",
    "configuration": "short human-readable config"
  }
}
```

If a system fails to complete, refuses, or returns output that cannot be parsed into the
requested schema, still write a result file with an empty `data` object:

```json
{
  "status": "failed",
  "error": {
    "type": "schema_mismatch",
    "message": "model output did not conform to the schema"
  },
  "data": {},
  "meta": {
    "model": "model-or-api-version"
  }
}
```

This makes the failure visible and causes all non-empty labeled fields for that document
to be counted as errors.

## Validation And Scoring

Run:

```bash
docubench validate
docubench score --engine <system_name>
```

To regenerate reports for all result sets:

```bash
docubench report
```

## Prompts And Run Configuration

Commit the prompt and run configuration that produced a submission under
[`prompts/`](../prompts), following the existing baselines. The committed LLM baselines
load their instruction from [`prompts/extraction_prompt.txt`](../prompts/extraction_prompt.txt)
at runtime, and each result file stamps its model id, provider, and `schema_mode` in
`meta` — together these make a result set reproducible and auditable.

## Review Expectations

Public submissions should state:

- system name and version
- model or provider version, if applicable
- extraction settings that affect output
- date of run
- whether the run was produced by released code, proprietary code, or manual workflow
- any documents skipped or failed
- cost and latency if measured
