from scorer import score_standardization


def test_array_matching_is_order_independent():
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                        "qty": {"type": "number"},
                    },
                },
            }
        },
    }
    label = {"items": [{"sku": "A-1", "qty": 2}, {"sku": "B-2", "qty": 3}]}
    result = {"items": [{"sku": "B 2", "qty": 3.0}, {"sku": "a1", "qty": 2}]}

    assert score_standardization(result=result, schema=schema, label=label)["final"] == 1.0


def test_both_blank_non_array_fields_are_skipped():
    schema = {
        "type": "object",
        "properties": {
            "empty": {"type": "string"},
            "name": {"type": "string"},
        },
    }
    label = {"empty": "", "name": "Acme, Inc."}
    result = {"empty": "", "name": "ACME INC"}

    out = score_standardization(result=result, schema=schema, label=label)

    assert out["non_array"]["total"] == 1
    assert out["final"] == 1.0


def test_extra_array_item_field_reduces_similarity():
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                    },
                },
            }
        },
    }
    label = {"items": [{"sku": "A"}]}
    result = {"items": [{"sku": "A", "extra": "not requested"}]}

    assert score_standardization(result=result, schema=schema, label=label)["final"] == 0.5
