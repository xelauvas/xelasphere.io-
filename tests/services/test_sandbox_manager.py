"""Regression tests for sandbox dependency detection and TTY-only library handling.

Covers the bug where `import curses` (a stdlib module) leaked into the pip
install list, producing: "ERROR: Could not find a version that satisfies the
requirement curses". And the follow-on: curses/TUI programs need an interactive
terminal that script mode can't provide, so they should be rejected with an
honest message instead of a silent exit-0.
"""
import sys

import pytest

from app.services.sandbox_manager import (
    detect_extra_packages,
    requires_interactive_terminal,
)


# ---- Problem 1: stdlib must never be sent to pip --------------------------

def test_curses_is_not_pip_installed():
    """The reported bug: `import curses` must not become `pip install curses`."""
    code = "import curses\nimport random\n"
    assert "curses" not in detect_extra_packages(code)


@pytest.mark.parametrize(
    "module",
    ["tty", "termios", "select", "signal", "ctypes", "asyncio",
     "subprocess", "multiprocessing", "ssl", "platform"],
)
def test_other_stdlib_modules_not_pip_installed(module):
    """The allowlist was partial; any stdlib module must be filtered out."""
    code = f"import {module}\n"
    assert module not in detect_extra_packages(code)


def test_every_stdlib_name_is_filtered():
    """Belt and suspenders: nothing in sys.stdlib_module_names reaches pip."""
    code = "".join(
        f"import {m}\n" for m in sys.stdlib_module_names if not m.startswith("_")
    )
    leaked = [m for m in detect_extra_packages(code) if m in sys.stdlib_module_names]
    assert leaked == []


def test_genuine_third_party_packages_still_detected():
    """We must not over-filter: real PyPI deps still need installing."""
    code = "import pyfiglet\nimport curses\nimport os\n"
    extra = detect_extra_packages(code)
    assert "pyfiglet" in extra
    assert "curses" not in extra
    assert "os" not in extra


def test_preinstalled_third_party_still_skipped():
    """Pre-baked image packages (numpy, etc.) are not reinstalled."""
    code = "import numpy\nimport pandas\nfrom bs4 import BeautifulSoup\n"
    assert detect_extra_packages(code) == []


# ---- Problem 2: terminal-interactive libs need a TTY ---------------------

def test_curses_requires_interactive_terminal():
    code = "import curses\ndef main(s):\n    pass\ncurses.wrapper(main)\n"
    assert requires_interactive_terminal(code) == "curses"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("from curses import wrapper\n", "curses"),
        ("import urwid\n", "urwid"),
        ("import blessed\n", "blessed"),
        ("from prompt_toolkit import prompt\n", "prompt_toolkit"),
        ("import textual\n", "textual"),
    ],
)
def test_tui_libraries_require_interactive_terminal(code, expected):
    assert requires_interactive_terminal(code) == expected


def test_plain_script_does_not_require_terminal():
    code = "import math\nprint(math.sqrt(2))\n"
    assert requires_interactive_terminal(code) is None


def test_substring_does_not_false_positive():
    """`recurses` / a variable named `curses_count` must not trip detection."""
    code = "def recurses():\n    pass\ncurses_count = 0\n"
    assert requires_interactive_terminal(code) is None
