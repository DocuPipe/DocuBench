from pathlib import Path

from docubench.cli import score_engines, validate_benchmark


ROOT = Path(__file__).resolve().parents[1]


def test_validate_current_benchmark_files():
    errors, warnings, summary = validate_benchmark(ROOT)

    assert errors == []
    assert warnings == []
    assert summary["documents"] == 50
    assert summary["labels"] == 50
    assert summary["schemas"] == 50
    assert summary["sources"] == 50
    assert set(summary["engines"]) == {
        "claude",
        "claude5",
        "docupipe_high",
        "docupipe_standard",
        "extend",
        "gemini",
        "gpt",
        "reducto",
        "reducto_standard",
    }


def test_score_engines_reproduces_extend_aggregate():
    scores = score_engines(ROOT, ["extend"])

    assert round(scores["aggregates"]["extend"], 4) == 0.9111
    assert len(scores["per_doc"]) == 50
