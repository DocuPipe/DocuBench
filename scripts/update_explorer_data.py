#!/usr/bin/env python3
"""refresh the embedded DATA blob in docubench-explorer.html from results/summary.json.

the explorer is a single self-contained html file; its per-document scores live in a
`const DATA = {...}` blob. curated per-doc metadata (name, feature, capability flags) lives
only here, so this updater preserves those fields and rewrites only the per-engine scores +
the display-name map from the canonical summary.json. run after `docubench report`:

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
MARKER = "const DATA = "

# fields carried over unchanged from the existing blob (everything that is not a score)
META_FIELDS = ("id", "name", "lang", "ftype", "pages", "feature", "flags")


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

    missing = [i for i in old_docs if i not in per_doc]
    if missing:
        raise SystemExit(f"docs in explorer but not in summary: {missing}")

    new_docs = []
    for doc_id, old in old_docs.items():
        row = {field: old[field] for field in META_FIELDS}
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
