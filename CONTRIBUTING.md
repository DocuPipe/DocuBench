# Contributing

DocuBench is intended to be reproducible benchmark infrastructure. Contributions should preserve that property.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
```

## Checks

Run these before opening a pull request:

```bash
docubench validate
docubench score
pytest
```

## Result Submissions

Add new system outputs under `results/<system_name>/`. Each `<doc_id>.json` file should include a top-level `data` object. See [`docs/submissions.md`](docs/submissions.md).

If your system uses a prompt or run configuration, commit it under [`prompts/`](prompts) alongside the existing baselines so the result set is reproducible and auditable.

## Leaderboard Data

The Hugging Face Space in [`space/`](space) renders `space/leaderboard.json`. After changing results or labels, refresh it so the leaderboard stays in sync:

```bash
docubench report
python3 space/build_data.py
```

`tests/test_space.py` fails if the committed `space/leaderboard.json` is stale.

## Dataset Changes

Changes to documents, schemas, labels, or scoring behavior can change benchmark meaning. Include:

- the reason for the change
- affected document IDs
- before/after scoring impact if applicable
- source and license details for any new document

Scoring changes should be treated as benchmark-version changes.
