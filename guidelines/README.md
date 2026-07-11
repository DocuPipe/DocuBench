# Schema Guidelines

These files contain the per-schema extraction guidelines used for the public 50-document benchmark. Direct LLM runners
append `guidelines/<doc_id>.txt` to the shared extraction prompt when the file is non-empty. The Extend runner sends the
same text as extractor `extractionRules`.

Empty files mean the corresponding schema has no separate guideline text beyond the JSON Schema descriptions.
