import importlib.util


def load_run_claude_module():
    spec = importlib.util.spec_from_file_location("run_claude", "scripts/run_claude.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_claude_schema_uses_optional_non_null_enums():
    run_claude = load_run_claude_module()
    schema = {
        "name": "Lease Schema",
        "type": "object",
        "properties": {
            "rateUnit": {
                "type": ["string", "null"],
                "description": "Unit the rates are expressed in",
                "enum": ["NIS per square meter", "NIS", None],
            }
        },
        "required": ["rateUnit"],
    }

    normalized = run_claude.normalize_claude_output_schema(schema)
    assert "name" not in normalized
    assert normalized["required"] == ["rateUnit"]
    field_schema = normalized["properties"]["rateUnit"]

    assert field_schema["description"] == "Unit the rates are expressed in"
    assert field_schema["type"] == "string"
    assert field_schema["enum"] == ["NIS per square meter", "NIS"]


def test_claude_result_validation_fills_omitted_nullable_fields():
    run_claude = load_run_claude_module()
    schema = {
        "type": "object",
        "properties": {
            "invoiceNumber": {"type": ["string", "null"]},
            "total": {"type": ["number", "null"]},
        },
        "required": ["invoiceNumber", "total"],
        "additionalProperties": False,
    }

    filled = run_claude.fill_missing_nullable_fields({"invoiceNumber": "INV-1"}, schema)

    assert filled == {"invoiceNumber": "INV-1", "total": None}


def test_claude_json_parser_repairs_hebrew_abbreviation_quotes():
    run_claude = load_run_claude_module()
    text = '{"amountUnits":"אלפי ש"ח","organizationName":"עמותה (ע"ר)"}'

    parsed = run_claude.parse_json_text(text)

    assert parsed == {"amountUnits": 'אלפי ש"ח', "organizationName": 'עמותה (ע"ר)'}


def test_claude_response_validation_preserves_quoted_hebrew_enums():
    run_claude = load_run_claude_module()
    message = {"content": [{"type": "text", "text": '{"amountUnits":"ש"ח"}'}]}
    schema = {
        "type": "object",
        "properties": {
            "amountUnits": {
                "type": "string",
                "enum": ['ש"ח', 'אלפי ש"ח'],
            }
        },
    }

    parsed = run_claude.parse_response_data(message=message, output_schema=schema)

    assert parsed == {"amountUnits": 'ש"ח'}


def test_claude_validation_schema_preserves_enum_quotes_and_allows_null():
    run_claude = load_run_claude_module()
    schema = {
        "type": "object",
        "properties": {
            "amountUnits": {
                "type": "string",
                "enum": ['ש"ח', 'אלפי ש"ח'],
            }
        },
    }

    normalized = run_claude.normalize_claude_validation_schema(schema)

    field_schema = normalized["properties"]["amountUnits"]
    assert field_schema["type"] == ["string", "null"]
    assert field_schema["enum"] == ['ש"ח', 'אלפי ש"ח', None]
