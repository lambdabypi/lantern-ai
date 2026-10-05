"""The mascot SVG must keep the layer ids the renderers animate (template now, real art later)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

MASCOT_DIR = Path(__file__).resolve().parents[1] / "app" / "web" / "mascot"
REQUIRED_LAYERS = {
    "shadow", "body", "core", "core-icon", "highlight", "outline",
    "eyes-open", "eyes-closed", "mouth-neutral", "mouth-smile", "mouth-o", "cheeks", "paws",
}
SVG = "{http://www.w3.org/2000/svg}"
FORBIDDEN = {"filter", "text", "mask"}  # Qt's SVG Tiny renderer ignores filters/masks; no baked-in text


def art_files() -> list[Path]:
    files = [MASCOT_DIR / "glim-template.svg"]
    if (MASCOT_DIR / "glim.svg").exists():
        files.append(MASCOT_DIR / "glim.svg")
    return files


@pytest.mark.parametrize("path", art_files(), ids=lambda p: p.name)
def test_svg_has_required_layers_and_size(path):
    root = ET.parse(path).getroot()
    assert root.get("viewBox") == "0 0 256 256"
    ids = {el.get("id") for el in root.iter() if el.get("id")}
    missing = REQUIRED_LAYERS - ids
    assert not missing, f"{path.name} is missing layers: {sorted(missing)}"


@pytest.mark.parametrize("path", art_files(), ids=lambda p: p.name)
def test_svg_uses_only_renderer_safe_features(path):
    root = ET.parse(path).getroot()
    used = {el.tag.removeprefix(SVG) for el in root.iter()} & FORBIDDEN
    assert not used, f"{path.name} uses unsupported elements: {sorted(used)}"
    if path.name == "glim.svg":
        assert root.find(f".//{SVG}g[@id='guides']") is None, "remove the guides layer before exporting"
