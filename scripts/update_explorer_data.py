#!/usr/bin/env python3
"""refresh the embedded DATA blob in docubench-explorer.html from results/summary.json.

the explorer is a single self-contained html file; its per-document scores live in a
`const DATA = {...}` blob. this updater preserves existing curated metadata, adds new docs
from sources.json, and rewrites the per-engine scores + display-name map from the canonical
summary.json. run after `docubench report`:

    python3 scripts/update_explorer_data.py

engine presentation (labels, ordering, colors) is configured by hand in the html's ENGINES /
ORDER / per-engine css blocks; add new engines there when they first appear.
"""
from __future__ import annotations

import json
from json import JSONDecoder
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPLORER = ROOT / "docubench-explorer.html"
SUMMARY = ROOT / "results" / "summary.json"
SOURCES = ROOT / "sources.json"
MARKER = "const DATA = "

# fields carried over unchanged from the existing blob (everything that is not a score)
META_FIELDS = ("id", "name", "lang", "ftype", "pages", "feature", "flags")
CAPABILITY_FLAGS = ("arrays", "reconcile", "rtl", "cjk", "handwriting", "rotated", "needle", "nested")


def source_metadata(source: dict) -> dict:
    """build explorer metadata for a document not already present in the html blob."""
    source_flags = source.get("flags") or {}
    return {
        "id": source["doc_id"],
        "name": source["name"],
        "lang": source["lang"],
        "ftype": source["ftype"],
        "pages": source["pages"],
        "feature": source.get("hard_feature", ""),
        "flags": {flag: bool(source_flags.get(flag, False)) for flag in CAPABILITY_FLAGS},
    }


def main() -> int:
    html = EXPLORER.read_text(encoding="utf-8")
    start = html.find(MARKER)
    if start == -1:
        raise SystemExit("could not find DATA marker in explorer html")
    brace = html.find("{", start)
    data, end = JSONDecoder().raw_decode(html[brace:])

    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    engines = list(summary["aggregates"].keys())
    per_doc = {row["doc_id"]: row for row in summary["per_doc"]}
    old_docs = {d["id"]: d for d in data["docs"]}
    sources = sorted(json.loads(SOURCES.read_text(encoding="utf-8")), key=lambda source: source["n"])
    source_ids = {source["doc_id"] for source in sources}

    missing_sources = sorted(set(per_doc) - source_ids)
    extra_sources = sorted(source_ids - set(per_doc))
    if missing_sources or extra_sources:
        raise SystemExit(f"sources/summary mismatch: missing={missing_sources}, extra={extra_sources}")

    new_docs = []
    for source in sources:
        doc_id = source["doc_id"]
        old = old_docs.get(doc_id)
        row = {field: old[field] for field in META_FIELDS} if old else source_metadata(source=source)
        scores = per_doc[doc_id]
        for engine in engines:
            row[engine] = scores.get(engine)
        new_docs.append(row)

    data["docs"] = new_docs
    data["display"] = dict(summary.get("engine_display_names", {}))
    data["meta"] = summary.get("benchmark", data.get("meta", {}))

    new_blob = json.dumps(data, ensure_ascii=False, separators=(", ", ": "))
    updated = html[:brace] + new_blob + html[brace + end:]
    EXPLORER.write_text(updated, encoding="utf-8")
    print(f"updated {EXPLORER.name}: {len(new_docs)} docs, engines={engines}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
