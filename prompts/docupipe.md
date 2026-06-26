# DocuPipe

- **Result sets:** `results/docupipe_high/<doc_id>.json`, `results/docupipe_standard/<doc_id>.json`
- **Runner:** none in this repository (vendor product)

## No natural-language prompt

DocuPipe is a hosted document-extraction product ([docupipe.ai](https://www.docupipe.ai)),
not a chat model, so there is no free-text prompt to commit. Each document was processed
through the product using the same benchmark schema (`schemas/<doc_id>.json`) as every
other system, with default extraction settings at two effort levels:

- **`docupipe_high`** — high-effort extraction
- **`docupipe_standard`** — standard-effort extraction

The two result sets exist so the cost/latency/accuracy trade-off of a single product is
visible on the same documents.

## Reproducibility note

DocuPipe's internal extraction pipeline is proprietary and is not run by code in this
repository. The committed result files are the product's raw outputs, scored by the same
public scorer against the same labels as all other systems. They are presented as
baseline submissions, not as a privileged reference — anyone can score a competing system
against these documents with `docubench score`. Each result file records its run
metadata (cost, time, effort level) in the result envelope.
