# Reducto

- **Runner:** [`scripts/run_reducto.py`](../scripts/run_reducto.py)
- **Result sets:** `results/reducto/<doc_id>.json` (Deep Extract) and `results/reducto_standard/<doc_id>.json` (standard)
- **API:** Reducto (`https://platform.reducto.ai`), async `/extract_async` + `/job/{id}` polling

## Two modes

Reducto is a configured extraction product, not a chat model, so there is no free-text prompt to
commit. The task is expressed through the JSON Schema passed on `instructions.schema`, plus a fixed
`instructions.system_prompt`. Both committed result sets use the same request shape and differ only
in one setting:

- **Deep Extract** (`results/reducto`) — `settings.deep_extract: true`, the agentic `super_agent`
  mode Reducto markets for its highest accuracy.
- **standard** (`results/reducto_standard`) — `settings.deep_extract: false`, the flat extract path.

The runner infers the mode from the output directory (`…/reducto_standard/` -> standard) and honors a
`REDUCTO_DEEP_EXTRACT=0/1` override.

## System prompt

A fixed, task-neutral instruction is sent on every run:

```text
Be precise and thorough. Extract exactly what appears in the document; do not infer or fabricate values.
```

When `guidelines/<doc_id>.txt` exists and is non-empty, it is appended after the fixed instruction —
the same schema-level steering text the LLM runners and Extend receive.

## Schema handling

Reducto accepts standard JSON Schema including nested arrays-of-objects, so no structural rewrite is
needed (unlike Extend). `clean_node` only strips non-standard / leaky keys (`$schema`, `examples`,
`default`, `title`, and any `x_*` keys) before passing the schema through on `instructions.schema`.

## File handling

Reducto rejects two file shapes it can otherwise read once trivially re-encoded; the runner applies
the same transforms the benchmark used so the content is reachable, and records which transform ran in
`meta.file_handling`:

- **xml / json** uploads as `.txt` — Reducto's pipeline rejects the raw file type with "could not be
  processed", but the identical bytes uploaded as `.txt` extract fine.
- **oversized raw images** are re-encoded to a smaller jpeg (full resolution, lower quality) under a
  ~1.5 MB size cap; Reducto rejects large raw images on a size cap, not a format one.

These are documented as real customer-facing UX friction, not scored failures — a user with an XML
file or a high-DPI scan hits a cryptic error unless they know to rename or re-export.

## Run procedure

Each run uploads the source file fresh via `/upload` (preferring the returned `file_id`), submits
`/extract_async` with `input` + `instructions.schema` + `instructions.system_prompt` +
`settings.deep_extract`, polls `/job/{id}` until completion, and unwraps the single-record result into
the original field shape. Cost is derived from reported credits at `$0.015`/credit; Reducto bills
per-field and reports usage after the run, so exact per-doc cost is only known post-hoc.

## Run knobs (environment)

- `REDUCTO_API_KEY` (required, for your own Reducto workspace)
- `REDUCTO_DEEP_EXTRACT=0/1` (force standard / deep; otherwise inferred from the output dir)
- `REDUCTO_POLL_TIMEOUT_SEC` (default 1200; Deep Extract latency on large docs is highly variable)
