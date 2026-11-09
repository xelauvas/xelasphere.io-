"""The stream must carry the model text unchanged (live view == reloaded view)."""
from app.services.stream_normalizer import FenceAwareParagraphNormalizer


def _run(chunks):
    n = FenceAwareParagraphNormalizer()
    return "".join(n.feed(c) for c in chunks) + n.flush()


def test_table_rows_stay_on_consecutive_lines():
    table = "| a | b |\\n| --- | --- |\\n| 1 | 2 |\\n"
    assert _run([table]) == table


def test_code_is_untouched_even_when_the_fence_is_split_across_chunks():
    chunks = ["intro\\n``", "`py\\nx = 1\\ny = 2\\n`", "``\\nafter\\n"]
    assert _run(chunks) == "".join(chunks)


def test_list_continuation_lines_and_verse_keep_single_newlines():
    text = "1. Step\\n   detail line\\n2. Next\\n\\nRoses are red,\\nViolets are blue.\\n"
    assert _run(list(text)) == text


def test_empty_chunks_are_harmless():
    assert _run(["", None, "x"]) == "x"
