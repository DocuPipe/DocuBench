# GPT (OpenAI)

- **Runner:** [`scripts/run_gpt.py`](../scripts/run_gpt.py)
- **Result set:** `results/gpt/<doc_id>.json`
- **Default model:** `gpt-5.5` (override with `OPENAI_MODEL`)
- **API:** OpenAI Responses API (`/v1/responses`) with Structured Outputs
- **Schema mode:** `openai_strict_nullable_v1`

## Prompt

The user instruction is loaded from [`extraction_prompt.txt`](extraction_prompt.txt):

```text
Extract the document into the supplied JSON schema. Use only information present in the
document. Return null for fields that are not printed or cannot be determined. Preserve
table rows as arrays and preserve the document language for values. Document id: {doc_id}.
```

The document is attached as an `input_file` (PDF / native files via the Files API), an
`input_image` (JPEG/PNG/WebP/GIF), or a sequence of PNG pages converted from multipage
TIFF.

## Output constraint

The schema is sent as `text.format` of type `json_schema` with `strict: true`. The raw
benchmark schema is first normalized (`normalize_output_schema`) into OpenAI's strict
subset:

- drop `$schema`, `examples`, `default`, `title`, `format`, and any `x_*` keys
- every object lists all properties in `required` and sets `additionalProperties: false`
- objects and primitives are made nullable so a labeled `null` can be returned
- enum literals are sanitized and gain a `null` member when nullable

## Run knobs (environment)

- `OPENAI_API_KEY` (required)
- `OPENAI_MODEL`, `OPENAI_API_BASE`
- `OPENAI_MAX_OUTPUT_TOKENS`, `OPENAI_REASONING_EFFORT`
- `OPENAI_INPUT_USD_PER_1M`, `OPENAI_OUTPUT_USD_PER_1M` (optional cost estimate)

## Failure handling

API errors, refusals, empty output, invalid JSON, or schema-validation failures are
written as `status: "failed"` with `data: {}`, so the scorer counts every labeled field
as an error instead of silently dropping the document.
