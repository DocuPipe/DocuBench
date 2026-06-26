import importlib.util
from pathlib import Path

from PIL import Image


def load_run_gpt_module():
    spec = importlib.util.spec_from_file_location("run_gpt", "scripts/run_gpt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_prompt_uses_committed_template():
    run_gpt = load_run_gpt_module()
    template = Path("prompts/extraction_prompt.txt").read_text(encoding="utf-8").strip()

    # the committed prompt file is the single source of truth, not a stale copy
    assert run_gpt.load_prompt_template() == template
    assert "{doc_id}" in template
    assert run_gpt.build_prompt("ABC123") == template.format(doc_id="ABC123")
    assert "ABC123" in run_gpt.build_prompt("ABC123")


def test_tiff_is_converted_to_ordered_png_image_parts(tmp_path):
    tiff_path = tmp_path / "multi_page.tiff"
    first = Image.new("RGB", (10, 10), "white")
    second = Image.new("RGB", (10, 10), "black")
    first.save(tiff_path, save_all=True, append_images=[second])

    run_gpt = load_run_gpt_module()
    parts, meta = run_gpt.tiff_png_parts(tiff_path)

    assert meta == {"input_mode": "tiff_png_sequence", "tiff_pages": 2}
    assert [part["type"] for part in parts] == [
        "input_text",
        "input_image",
        "input_text",
        "input_image",
    ]
    assert parts[1]["image_url"].startswith("data:image/png;base64,")
    assert parts[3]["image_url"].startswith("data:image/png;base64,")
