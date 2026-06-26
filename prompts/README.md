# Prompts

This directory commits the exact instructions and run configuration used to produce
every committed baseline result set. Nothing here is decorative: the model-based runners
in [`scripts/`](../scripts) load [`extraction_prompt.txt`](extraction_prompt.txt) at
runtime, so the file in this directory is provably the prompt that was sent.

A benchmark number is only meaningful if you can see the prompt, the model, and the
schema handling that produced it. Models drift and providers deprecate versions, so each
committed result file also stamps the model id, provider, and `schema_mode` in its
`meta` block — pair that with the files here to reproduce or audit any score.

## Canonical extraction prompt

All three LLM runners (OpenAI, Anthropic, Google) send the same user instruction,
templated on `extraction_prompt.txt`. It is deliberately minimal — the JSON Schema
carries the field-level intent, and structured-output / response-schema modes enforce
shape — so the prompt does not leak task-specific hints that would inflate scores.

```text
Extract the document into the supplied JSON schema. Use only information present in the
document. Return null for fields that are not printed or cannot be determined. Preserve
table rows as arrays and preserve the document language for values. Document id: {doc_id}.
```

`{doc_id}` is filled with the benchmark document id at runtime.

## Per-system configuration

| System | Result dir | Runner | Prompt / config |
|---|---|---|---|
| OpenAI GPT | `results/gpt` | [`scripts/run_gpt.py`](../scripts/run_gpt.py) | [`gpt.md`](gpt.md) |
| Anthropic Claude | `results/claude` | [`scripts/run_claude.py`](../scripts/run_claude.py) | [`claude.md`](claude.md) |
| Google Gemini | `results/gemini` | [`scripts/run_gemini.py`](../scripts/run_gemini.py) | [`gemini.md`](gemini.md) |
| Extend | `results/extend` | [`scripts/run_extend.py`](../scripts/run_extend.py) | [`extend.md`](extend.md) |
| DocuPipe | `results/docupipe_*` | (vendor product) | [`docupipe.md`](docupipe.md) |

## How the schema is used

The raw `schemas/<doc_id>.json` is JSON Schema draft-07. Each runner transforms it into
the strict subset its provider accepts (every object property required, objects/primitives
made nullable, unsupported keywords stripped) before constraining the model output. The
transform is part of the runner and is described in each per-system file. Extend and
DocuPipe consume the schema through their own extraction configuration rather than a
free-text prompt.
