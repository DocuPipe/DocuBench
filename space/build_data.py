"""Build the self-contained leaderboard.json that the Hugging Face Space renders.

The Space must run standalone on Hugging Face with no access to the parent repository, so
this script merges the repo's canonical artifacts into a single file committed alongside
the app:

  - results/summary.json         -> scores, aggregates, file-type / language breakdowns
  - sources.json                 -> per-document provenance (name, license, source url)
  - docubench-explorer.html     -> per-document capability flags (arrays, RTL, CJK, ...)
  - results/<engine>/<id>.json   -> the model id stamped in each result's meta block

Scores always come from summary.json (the output of `docubench report`); this script
never recomputes them. Run it from anywhere after regenerating the report:

    python3 space/build_data.py
"""
from __future__ import annotations

import json
from json import JSONDecoder
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SPACE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = SPACE_DIR / "leaderboard.json"

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


def load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract_explorer_flags(html_path: Path) -> dict[str, dict[str, bool]]:
    """Pull the per-document capability flags out of the explorer's embedded DATA blob."""
    if not html_path.exists():
        return {}
    text = html_path.read_text(encoding="utf-8")
    marker = "const DATA = "
    start = text.find(marker)
    if start == -1:
        return {}
    brace = text.find("{", start)
    data, _ = JSONDecoder().raw_decode(text[brace:])
    flags: dict[str, dict[str, bool]] = {}
    for doc in data.get("docs", []):
        if "id" in doc and isinstance(doc.get("flags"), dict):
            flags[doc["id"]] = {k: bool(v) for k, v in doc["flags"].items()}
    return flags


def engine_model(engine: str, doc_ids: list[str]) -> str | None:
    """Best-effort model id from the first result file that records one in meta."""
    for doc_id in doc_ids:
        path = REPO_ROOT / "results" / engine / f"{doc_id}.json"
        if not path.exists():
            continue
        try:
            meta = (load_json(path) or {}).get("meta") or {}
        except (json.JSONDecodeError, OSError):
            continue
        if meta.get("model"):
            return str(meta["model"])
    return None


def mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def build() -> dict[str, Any]:
    summary = load_json(REPO_ROOT / "results" / "summary.json")
    sources = {row["doc_id"]: row for row in load_json(REPO_ROOT / "sources.json") if "doc_id" in row}
    flags_by_doc = extract_explorer_flags(REPO_ROOT / "docubench-explorer.html")

    engines = list(summary["aggregates"].keys())
    display_names = summary.get("engine_display_names", {})
    per_doc_scores = {row["doc_id"]: row for row in summary["per_doc"]}
    doc_ids = [row["doc_id"] for row in summary["per_doc"]]

    documents = []
    for doc_id in doc_ids:
        src = sources.get(doc_id, {})
        scores = {engine: per_doc_scores[doc_id].get(engine) for engine in engines}
        documents.append({
            "doc_id": doc_id,
            "name": src.get("name", doc_id),
            "lang": src.get("lang", "unknown"),
            "ftype": src.get("ftype", "unknown"),
            "pages": src.get("pages"),
            "feature": src.get("hard_feature", ""),
            "license": src.get("license", ""),
            "source_url": src.get("source_url", ""),
            "flags": flags_by_doc.get(doc_id, {}),
            "scores": scores,
        })

    # capability breakdown: per-engine mean over the documents carrying each flag
    capability_rows = []
    for flag, label in CAPABILITY_LABELS.items():
        matching = [d for d in documents if d["flags"].get(flag)]
        if not matching:
            continue
        row: dict[str, Any] = {"flag": flag, "label": label, "doc_count": len(matching)}
        for engine in engines:
            row[engine] = mean([d["scores"].get(engine) for d in matching])
        capability_rows.append(row)

    return {
        "benchmark": summary.get("benchmark", {}),
        "engines": [
            {
                "key": engine,
                "display": display_names.get(engine, engine),
                "model": engine_model(engine, doc_ids),
                "overall": summary["aggregates"][engine],
            }
            for engine in engines
        ],
        "breakdowns": {
            "ftype": summary["breakdowns"].get("ftype", []),
            "lang": summary["breakdowns"].get("lang", []),
            "capability": capability_rows,
        },
        "documents": documents,
    }


def main() -> int:
    data = build()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(
        f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}: "
        f"{len(data['engines'])} engines, {len(data['documents'])} documents, "
        f"{len(data['breakdowns']['capability'])} capability rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
