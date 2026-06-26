"""DocuBench leaderboard — a Hugging Face Space (Gradio).

Renders the committed benchmark results from leaderboard.json (built by build_data.py from
the repo's canonical artifacts). The data functions below are deliberately framework-free
so they can be imported and tested without launching Gradio.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

DATA_PATH = Path(__file__).resolve().parent / "leaderboard.json"

CAPABILITY_LABELS = {
    "arrays": "Array / line-item tables",
    "reconcile": "Totals must reconcile",
    "rtl": "Right-to-left script",
    "cjk": "CJK script",
    "handwriting": "Handwriting",
    "rotated": "Rotated scan",
    "needle": "Needle-in-haystack lookup",
    "nested": "Nested objects",
}


def load_data() -> dict:
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


DATA = load_data()
ENGINES = DATA["engines"]
ENGINE_KEYS = [e["key"] for e in ENGINES]
ENGINE_LABEL = {e["key"]: e["display"] for e in ENGINES}


def pct(value) -> str:
    return "" if value is None else f"{value * 100:.2f}"


def leaderboard_df() -> pd.DataFrame:
    ranked = sorted(ENGINES, key=lambda e: (e["overall"] is not None, e["overall"]), reverse=True)
    rows = []
    for rank, engine in enumerate(ranked, start=1):
        rows.append({
            "Rank": rank,
            "System": engine["display"],
            "Model": engine.get("model") or "—",
            "Accuracy %": pct(engine["overall"]),
        })
    return pd.DataFrame(rows)


def _breakdown_df(rows: list[dict], label_key: str, label_header: str) -> pd.DataFrame:
    out = []
    for row in rows:
        record = {label_header: row[label_key], "Docs": row.get("doc_count", "")}
        for key in ENGINE_KEYS:
            record[ENGINE_LABEL[key]] = pct(row.get(key))
        out.append(record)
    return pd.DataFrame(out)


def filetype_df() -> pd.DataFrame:
    return _breakdown_df(DATA["breakdowns"]["ftype"], "value", "File type")


def language_df() -> pd.DataFrame:
    return _breakdown_df(DATA["breakdowns"]["lang"], "value", "Language")


def capability_df() -> pd.DataFrame:
    return _breakdown_df(DATA["breakdowns"]["capability"], "label", "Capability")


def documents_df(ftype: str = "All", capability: str = "All") -> pd.DataFrame:
    rows = []
    for doc in DATA["documents"]:
        if ftype != "All" and doc["ftype"] != ftype:
            continue
        if capability != "All" and not doc["flags"].get(capability):
            continue
        record = {
            "Document": doc["name"],
            "Type": doc["ftype"],
            "Lang": doc["lang"],
            "Pages": doc.get("pages") or "",
            "Challenge": doc.get("feature", ""),
        }
        for key in ENGINE_KEYS:
            record[ENGINE_LABEL[key]] = pct(doc["scores"].get(key))
        rows.append(record)
    return pd.DataFrame(rows)


def filetype_choices() -> list[str]:
    return ["All"] + sorted({d["ftype"] for d in DATA["documents"]})


def capability_choices() -> list[tuple[str, str]]:
    present = {flag for d in DATA["documents"] for flag, on in d["flags"].items() if on}
    return [("All", "All")] + [(CAPABILITY_LABELS.get(f, f), f) for f in CAPABILITY_LABELS if f in present]


INTRO = f"""
# 📄 DocuBench Leaderboard

**{DATA['benchmark'].get('doc_count', len(DATA['documents']))} hard, real-world documents · schema-guided structured extraction · v{DATA['benchmark'].get('version', '?')}**

Each task pairs a source document with a JSON Schema and a hand-verified JSON label.
Systems are scored on field-level accuracy with order-independent array matching
(metric: `{DATA['benchmark'].get('metric', 'macro_average_field_accuracy')}`). Every number
here is produced by the same public scorer against the same labels — these are baseline
submissions, not a closed leaderboard.

Code, data, scorer, and committed prompts: **[github.com/DocuPipe/docubench](https://github.com/DocuPipe/docubench)**
"""

ABOUT = """
## How systems are scored

For each document a system receives the source file plus the paired JSON Schema and must
return JSON matching the schema. The scorer compares it to the hand-verified label:

- strings are normalized for whitespace, punctuation, and case
- numbers are cast to float and rounded
- arrays are matched order-independently (greedy best-pair assignment)
- the headline number is the macro average of per-document field accuracy

The 50 documents are deliberately hard: arrays and multi-page tables, totals that must
reconcile, right-to-left and CJK scripts, rotated scans, handwriting, and ten file types
(PDF, JPEG, PNG, TIFF, XLSX, CSV, XML, TXT, DOCX, HTML).

## Submitting a new system

1. Run your system over the 50 documents using the paired schemas.
2. Write `results/<your_system>/<doc_id>.json` with a top-level `data` object.
3. `docubench validate && docubench score --engine <your_system>`
4. Open a pull request.

The exact prompts and run configuration for every committed baseline live in
[`prompts/`](https://github.com/DocuPipe/docubench/tree/main/prompts), and the scoring
contract is in [`docs/scoring.md`](https://github.com/DocuPipe/docubench/blob/main/docs/scoring.md).

This leaderboard is a static view of the committed results; rebuild it with
`python3 space/build_data.py` after `docubench report`.
"""


def build_demo():
    import gradio as gr

    cap_choices = capability_choices()
    with gr.Blocks(title="DocuBench Leaderboard") as demo:
        gr.Markdown(INTRO)

        with gr.Tab("Leaderboard"):
            gr.Dataframe(value=leaderboard_df(), interactive=False, wrap=True)

        with gr.Tab("By file type"):
            gr.Dataframe(value=filetype_df(), interactive=False, wrap=True)

        with gr.Tab("By language"):
            gr.Dataframe(value=language_df(), interactive=False, wrap=True)

        with gr.Tab("By capability"):
            gr.Markdown("Accuracy on the documents that carry each hard capability.")
            gr.Dataframe(value=capability_df(), interactive=False, wrap=True)

        with gr.Tab("Documents"):
            with gr.Row():
                ftype = gr.Dropdown(choices=filetype_choices(), value="All", label="File type")
                capability = gr.Dropdown(choices=cap_choices, value="All", label="Capability")
            table = gr.Dataframe(value=documents_df(), interactive=False, wrap=True)
            for control in (ftype, capability):
                control.change(documents_df, inputs=[ftype, capability], outputs=table)

        with gr.Tab("About / Submit"):
            gr.Markdown(ABOUT)

    return demo


if __name__ == "__main__":
    import gradio as gr

    build_demo().launch(theme=gr.themes.Soft())
