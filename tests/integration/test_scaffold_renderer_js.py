"""W-23 smoke test — exercises scaffoldRenderer.js via Node subprocess.

No Jest/jsdom in this repo; instead we run Node with a tiny DOM stub
and assert on rendered output. Covers the shape guarantees:
  - opening paragraph present
  - section headers rendered with emoji
  - list/steps/prose dispatch
  - separator between opening and sections
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


RENDERER_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "scaffoldRenderer.js"
)


pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="Node.js not available — JS smoke skipped.",
)


_DOM_STUB = r"""
// Minimal DOM stub — enough for scaffoldRenderer to walk over.
class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this._textContent = '';
    this.firstChild = null;
  }
  get textContent() { return this._textContent; }
  set textContent(v) { this._textContent = String(v); }
  appendChild(child) {
    this.children.push(child);
    this.firstChild = this.children[0] || null;
    return child;
  }
  removeChild(child) {
    const i = this.children.indexOf(child);
    if (i >= 0) this.children.splice(i, 1);
    this.firstChild = this.children[0] || null;
  }
  serialize() {
    const s = {
      tag: this.tagName,
      text: this._textContent || null,
      children: this.children.map(function (c) { return c.serialize(); }),
    };
    return s;
  }
}
globalThis.document = {
  createElement: function (tag) { return new FakeElement(tag); },
};
globalThis.window = { scaffoldRenderer: null };
"""


def _run_render(data: dict) -> dict:
    script = f"""
{_DOM_STUB}
const {{ renderStructuredResponse }} = require('{RENDERER_PATH}');
const root = new FakeElement('div');
renderStructuredResponse({json.dumps(data)}, root);
process.stdout.write(JSON.stringify(root.serialize()));
"""
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"node exited non-zero: {result.stderr}"
    return json.loads(result.stdout)


def test_renders_opening_paragraph():
    tree = _run_render({"opening": "Quick hello.", "sections": [], "closing": ""})
    assert tree["children"][0]["tag"] == "P"
    assert tree["children"][0]["text"] == "Quick hello."


def test_renders_section_headers_with_emoji():
    tree = _run_render({
        "opening": "",
        "sections": [
            {"title": "History", "emoji": "📜", "body": "Once.", "style": "prose"},
            {"title": "Future",  "emoji": "🚀", "body": "Later.", "style": "prose"},
        ],
        "closing": "",
    })
    h2s = [c for c in tree["children"] if c["tag"] == "H2"]
    assert len(h2s) == 2
    assert h2s[0]["text"] == "📜 History"
    assert h2s[1]["text"] == "🚀 Future"


def test_renders_list_style_as_ul():
    tree = _run_render({
        "opening": "",
        "sections": [{"title": "Items", "emoji": "", "body": "- a\n- b\n- c", "style": "list"}],
        "closing": "",
    })
    uls = [c for c in tree["children"] if c["tag"] == "UL"]
    assert len(uls) == 1
    assert len(uls[0]["children"]) == 3
    assert uls[0]["children"][0]["text"] == "a"


def test_renders_steps_style_as_ol():
    tree = _run_render({
        "opening": "",
        "sections": [{"title": "Steps", "emoji": "", "body": "1. first\n2. second", "style": "steps"}],
        "closing": "",
    })
    ols = [c for c in tree["children"] if c["tag"] == "OL"]
    assert len(ols) == 1
    assert len(ols[0]["children"]) == 2


def test_inserts_separator_between_opening_and_sections():
    tree = _run_render({
        "opening": "Intro.",
        "sections": [{"title": "A", "emoji": "", "body": "Body.", "style": "prose"}],
        "closing": "",
    })
    tags = [c["tag"] for c in tree["children"]]
    # Order: P (opening) → HR → H2 (section)
    assert tags[0] == "P"
    assert tags[1] == "HR"
    assert tags[2] == "H2"


def test_no_separator_when_no_opening():
    tree = _run_render({
        "opening": "",
        "sections": [{"title": "A", "emoji": "", "body": "Body.", "style": "prose"}],
        "closing": "",
    })
    assert "HR" not in [c["tag"] for c in tree["children"]]


def test_handles_empty_data_gracefully():
    tree = _run_render({})
    assert tree["children"] == []


def test_prose_body_splits_on_blank_lines():
    tree = _run_render({
        "opening": "",
        "sections": [{"title": "Body", "emoji": "", "body": "First para.\n\nSecond para.", "style": "prose"}],
        "closing": "",
    })
    # Flat: H2, P, P
    tags = [c["tag"] for c in tree["children"]]
    assert tags.count("P") == 2
