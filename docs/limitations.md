# Limitations

DocuBench is intentionally hard, but it is not exhaustive.

## Dataset Size

The benchmark has 50 documents. That is enough for careful inspection and regression testing, but not enough to support broad statistical claims about every document domain.

## Public-Document Bias

All documents are public, publicly posted samples, openly licensed files, government publications, or benchmark-authored artifacts. This improves reproducibility, but it means the corpus may differ from private enterprise document distributions.

## Label And Schema Scope

Each schema asks for selected fields rather than every possible fact in a document. A system may extract useful information that is not measured by a given schema.

## Scoring Scope

The current scorer uses normalized exact matching and greedy array alignment. It does not yet support field-specific semantic matching, numeric tolerances beyond float normalization, or globally optimal array assignment.

## Aggregate Interpretation

The headline score is a macro average over documents. Users should inspect per-document and per-capability results before making system decisions.

## Cost And Latency

Some result files include cost and timing metadata, but the benchmark does not yet enforce a uniform cost/latency reporting contract across all systems.
