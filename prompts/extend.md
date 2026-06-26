# Extend

- **Runner:** [`scripts/run_extend.py`](../scripts/run_extend.py)
- **Result set:** `results/extend/<doc_id>.json`
- **API:** Extend (`https://api.extend.ai`), `x-extend-api-version: 2026-02-09`

## No natural-language prompt

Extend is a configured extraction product, not a chat model, so there is no free-text
prompt to commit. The task is expressed entirely through an **extractor** created from the
benchmark schema. The committed configuration is:

- `baseProcessor: extraction_performance`
- `baseVersion: 4.1.1`
- `parseConfig.engine: parse_performance`

## Schema handling

`schemas/<doc_id>.json` is transformed into Extend's accepted schema subset
(`transform_schema` / `transform_node`):

- strip `$schema`, `examples`, `default`, `title`, and any `x_*` keys
- primitives become nullable unions (`["string", "null"]`); enums gain a `null` member
- `format: "date"` becomes `extend:type: "date"`
- every object sets `additionalProperties: false`; arrays and nested objects recurse
- the reserved top-level property name `id` is renamed on the Extend side and mapped back
  in the output

## Run procedure

Each run uploads the source file with a **fresh `file_id`** (the first `/extract` on a
file id caches the parse output, so reusing it would silently serve a stale parse) and
creates a uniquely named extractor so the current schema is always used. The runner then
calls `/extract`, polls `/extract_runs/<id>` until `PROCESSED`, and maps the output back
to the original field names. Cost is derived from reported credits at
`$0.0125`/credit.

## Run knobs (environment)

- `EXTEND_API_KEY` (required, for your own Extend workspace)
