import json
from pathlib import Path

from docubench.cli import score_engines, validate_benchmark


ROOT = Path(__file__).resolve().parents[1]


def test_validate_current_benchmark_files():
    errors, warnings, summary = validate_benchmark(ROOT)

    assert errors == []
    # every committed engine now covers all 72 documents
    assert warnings == []
    assert summary["documents"] == 72
    assert summary["labels"] == 72
    assert summary["schemas"] == 72
    assert summary["sources"] == 72
    assert set(summary["engines"]) == {
        "claude5",
        "docupipe_high",
        "docupipe_standard",
        "extend",
        "gemini",
        "gpt",
        "reducto",
        "reducto_standard",
        "unstructured",
    }


def test_score_engines_reproduces_extend_aggregate():
    scores = score_engines(ROOT, ["extend"])
    summary = json.loads((ROOT / "results" / "summary.json").read_text())

    assert scores["aggregates"]["extend"] == summary["aggregates"]["extend"]
    assert len(scores["per_doc"]) == 72
