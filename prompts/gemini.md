# Gemini (Google)

- **Runner:** [`scripts/run_gemini.py`](../scripts/run_gemini.py)
- **Result set:** `results/gemini/<doc_id>.json`
- **Default model:** `gemini-3.5-flash` (override with `GEMINI_MODEL`)
- **API:** Generative Language API (`/v1beta/models/<model>:generateContent`)
- **Schema mode:** `gemini_response_schema_nullable_v1`

## Prompt

Identical to the canonical [`extraction_prompt.txt`](extraction_prompt.txt):

```text
Extract the document into the supplied JSON schema. Use only information present in the
document. Return null for fields that are not printed or cannot be determined. Preserve
table rows as arrays and preserve the document language for values. Document id: {doc_id}.
```

PDFs and images are sent as `inline_data`, multipage TIFF as a sequence of inline PNG
pages, and DOCX/XLSX/text formats as extracted plain text.

## Output constraint

`generationConfig.responseMimeType` is set to `application/json` and
`generationConfig.responseSchema` carries the schema rendered into Gemini's typed schema
form (`OBJECT`/`ARRAY`/`STRING`/`NUMBER`/`INTEGER`/`BOOLEAN`, `nullable` flags,
`propertyOrdering`). The same normalized strict schema used by the GPT runner is the
input to this conversion.

## Run knobs (environment)

- `GOOGLE_API_KEY` or `GEMINI_API_KEY` (required)
- `GEMINI_MODEL`, `GEMINI_API_BASE`, `GEMINI_MAX_OUTPUT_TOKENS`
- `GEMINI_INPUT_USD_PER_1M`, `GEMINI_OUTPUT_USD_PER_1M` (optional cost estimate)

## Failure handling

Missing candidates, non-`STOP` finish reasons, empty output, invalid JSON, and
schema-validation failures are written as `status: "failed"` with `data: {}`.
