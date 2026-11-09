"""FenceAwareParagraphNormalizer: paragraph breaks outside code, code untouched, any chunking."""
import random

from app.services.stream_normalizer import FenceAwareParagraphNormalizer, normalize_paragraph_breaks

TEXT = (
    "Here is code:\n```python\ndef f(n):\n    if n < 2:\n        return False\n    return True\n```\n"
    "It returns `True` for primes.\nSecond line."
)


def run(chunks):
    norm = FenceAwareParagraphNormalizer()
    return "".join(norm.feed(c) for c in chunks) + norm.flush()


def code_block(text):
    start = text.index("```python")
    return text[start:text.index("```", start + 3)]


def test_code_is_not_double_spaced_for_any_chunking():
    rng = random.Random(7)
    for _ in range(500):
        cuts = sorted(rng.sample(range(1, len(TEXT)), rng.randint(1, 14)))
        chunks = [TEXT[a:b] for a, b in zip([0] + cuts, cuts + [len(TEXT)])]
        out = run(chunks)
        assert "\n\n" not in code_block(out), chunks
        assert "`True`" in out


def test_prose_outside_code_still_gets_paragraph_breaks():
    out = run(["Para one.\nPara two.\n", "```py\nx=1\ny=2\n``", "`\nAfter."])
    assert out == "Para one.\n\nPara two.\n\n```py\nx=1\ny=2\n```\n\nAfter."


def test_trailing_inline_backtick_is_flushed():
    out = run(["Use `True`"])
    assert out.endswith("`True`")


def test_same_as_plain_normalizer_without_code():
    text = "One.\nTwo.\n- item\n- item\n# Head\nThree."
    assert run([text]) == normalize_paragraph_breaks(text)
