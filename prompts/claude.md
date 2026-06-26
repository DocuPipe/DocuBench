# Claude (Anthropic)

- **Runner:** [`scripts/run_claude.py`](../scripts/run_claude.py)
- **Result set:** `results/claude/<doc_id>.json`
- **Default model:** `claude-sonnet-4-6` (override with `ANTHROPIC_MODEL`)
- **API:** Anthropic Messages API (`/v1/messages`), `anthropic-version: 2023-06-01`
- **Schema mode:** `anthropic_output_config_nullable_v1`

## Prompt

The user instruction is the canonical [`extraction_prompt.txt`](extraction_prompt.txt)
with one trailing sentence added, because Claude returns conversational text by default:

```text
Extract the document into the supplied JSON schema. Use only information present in the
document. Return null for fields that are not printed or cannot be determined. Preserve
table rows as arrays and preserve the document language for values. Document id: {doc_id}.
Return only the JSON object, with no prose or markdown.
```

PDFs are sent as a base64 `document` block, images as `image` blocks, multipage TIFF as a
sequence of PNG `image` blocks, and DOCX/XLSX/text formats as extracted plain text.

## Output constraint

The normalized strict schema (shared with the GPT runner via `normalize_output_schema`)
is passed as `output_config.format` of type `json_schema`. If the provider rejects the
compiled grammar (for example a schema construct it cannot compile), the runner retries
once with the schema embedded in the prompt instead and records
`schema_fallback: "prompt_json_schema"` in `meta`. Code-fenced or prose-wrapped JSON is
unwrapped before parsing.

## Run knobs (environment)

- `ANTHROPIC_API_KEY` (required)
- `ANTHROPIC_MODEL`, `ANTHROPIC_API_BASE`, `ANTHROPIC_MAX_TOKENS`
- `ANTHROPIC_INPUT_USD_PER_1M`, `ANTHROPIC_OUTPUT_USD_PER_1M` (optional cost estimate)

## Failure handling

Truncation (`stop_reason` of `max_tokens`), empty output, invalid JSON, and
schema-validation failures are written as `status: "failed"` with `data: {}`.
