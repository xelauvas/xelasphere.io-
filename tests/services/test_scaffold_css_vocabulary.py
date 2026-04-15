"""Spec §8 risk: guard against Scaffold.layout drifting from CSS data-ctx rules."""
import re
from pathlib import Path
from app.services.chat_engine import ALLOWED_LAYOUTS


def test_every_allowed_layout_has_a_css_rule():
    css_path = Path(__file__).resolve().parents[2] / "app/static/css/stream-blocks.css"
    assert css_path.exists(), f"Missing CSS file: {css_path}"
    css = css_path.read_text(encoding="utf-8")
    defined_in_css = set(re.findall(r'\[data-ctx="([^"]+)"\]', css))
    missing = set(ALLOWED_LAYOUTS) - defined_in_css
    assert not missing, (
        f"Layouts in ALLOWED_LAYOUTS but NOT in stream-blocks.css: {missing}. "
        "Either add CSS rules or remove from ALLOWED_LAYOUTS."
    )
