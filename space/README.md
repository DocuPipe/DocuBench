---
title: DocuBench Leaderboard
emoji: 📄
colorFrom: indigo
colorTo: blue
sdk: gradio
sdk_version: 6.19.0
app_file: app.py
pinned: false
license: mit
---

# DocuBench Leaderboard

An interactive leaderboard for [DocuBench](https://github.com/DocuPipe/docubench), a
public benchmark for schema-guided structured extraction from 50 hard, real-world
documents. It shows the headline ranking plus breakdowns by file type, language, and hard
capability (arrays, reconciling totals, right-to-left and CJK scripts, rotated scans,
handwriting, and more), and a searchable per-document table.

Every number is produced by the benchmark's public scorer against the same hand-verified
labels. These are baseline submissions, not a closed leaderboard.

## Files in this Space

| File | Purpose |
|---|---|
| `app.py` | Gradio leaderboard application |
| `leaderboard.json` | Self-contained data the app renders |
| `requirements.txt` | Runtime dependencies |

`leaderboard.json` is generated from the benchmark repo by
[`space/build_data.py`](https://github.com/DocuPipe/docubench/blob/main/space/build_data.py),
which merges `results/summary.json` (scores), `sources.json` (provenance), and the
per-document capability flags. It is committed so the Space runs standalone.

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

## Deploy to Hugging Face

The Space needs `app.py`, `requirements.txt`, `leaderboard.json`, and this `README.md`.

```bash
pip install huggingface_hub
huggingface-cli login
huggingface-cli repo create docubench-leaderboard --type space --space_sdk gradio

# from the repo root, push the contents of space/ to the Space root
huggingface-cli upload <your-username>/docubench-leaderboard space . --repo-type space
```

Equivalently, clone the empty Space repo and copy these four files into it, then
`git push`. Hugging Face builds and serves the app automatically.

## Update the data

From the benchmark repository, after regenerating the report:

```bash
docubench report
python3 space/build_data.py
```

Then commit the refreshed `space/leaderboard.json` and push it to the Space.
