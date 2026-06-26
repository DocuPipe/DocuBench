# Scoring

DocuBench scores schema-guided extraction output against hand-verified JSON labels. The scorer is intentionally small and inspectable; the canonical implementation is [`scorer.py`](../scorer.py).

## Inputs

Each scoring call takes:

- `result`: the system output JSON object, usually from the top-level `data` key in `results/<system>/<doc_id>.json`
- `schema`: the JSON Schema in `schemas/<doc_id>.json`
- `label`: the hand-verified target JSON in `labels/<doc_id>.json`

The schema determines which fields are scored.

## Normalization

Before comparison:

- numeric values are cast to floats and rounded to six decimal places
- strings are lowercased
- whitespace is removed from strings
- punctuation and non-word separators are removed from strings
- empty strings, empty arrays, and empty objects are stripped
- `null` is preserved because it can represent an intentional labeled value

This normalization is meant to avoid penalizing harmless formatting differences. It is not semantic matching.

## Non-Array Fields

Non-array leaves are scored as binary exact matches after normalization.

- matching leaf: `1`
- mismatching leaf: `0`
- both sides empty: skipped
- one side empty: mismatch

The non-array score is `correct / total` over scored leaf fields.

## Array Fields

Arrays are scored order-independently.

1. Each labeled item is compared with each predicted item.
2. Item similarity is computed as binary leaf-field accuracy over the union of leaves present in either item.
3. The scorer greedily assigns the highest-scoring label/result item pairs without reusing either side.
4. Unmatched label or result items count against the score through the denominator.

Array scores are weighted by the average non-empty leaf count in labeled array items. This keeps the final score approximately leaf-weighted rather than array-count-weighted.

## Aggregate Metric

For one document, the final score is a weighted average across array and non-array components.

For a system, the headline benchmark score is the macro average of per-document final scores. Each document contributes equally to the headline aggregate.

## Known Tradeoffs

- Array matching is greedy, not globally optimal.
- String matching is normalized exact matching, not semantic equivalence.
- Extra fields outside the schema are ignored for non-array objects.
- Extra fields inside array items can affect item-level similarity because array item comparison uses the union of leaves.
- Field-specific tolerances are not yet expressed in schema metadata.

These tradeoffs are documented so scores are interpretable and reproducible. Changes to scoring behavior should be treated as benchmark-version changes.
