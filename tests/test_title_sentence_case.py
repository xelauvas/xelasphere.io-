"""Chat titles are sentence case: acronyms and names survive, Title Case is lowered."""
from app.title_generator import TitleGenerator

sc = TitleGenerator._sentence_case


def test_title_case_from_model_is_lowered_but_acronyms_kept():
    assert sc("Reverse SSH Tunnel Explained") == "Reverse SSH tunnel explained"


def test_mixed_case_words_are_kept():
    assert sc("GitHub Actions cache keys") == "GitHub Actions cache keys"


def test_first_letter_is_capitalized():
    assert sc("explică pe scurt ce e un reverse SSH") == "Explică pe scurt ce e un reverse SSH"


def test_all_caps_is_lowered():
    assert sc("WSL DISK ATTACH ACCESS DENIED") == "Wsl disk attach access denied"


def test_empty():
    assert sc("") == ""
