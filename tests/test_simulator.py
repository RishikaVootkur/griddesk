import pytest

from griddesk.eval.simulator import is_closing_only


@pytest.mark.parametrize(
    ("text", "closing"),
    [
        ("Thanks, that's all. Goodbye!", True),
        ("Great, thank you for your help.", True),
        ("Yes, please schedule the free meter check.", False),
        ("Can you waive it?", False),
        ("I'd like to formally dispute this bill.", False),
        ("Aguilar. I want this disputed.", False),
    ],
)
def test_stop_is_only_honored_on_a_pure_goodbye(text, closing):
    assert is_closing_only(text) is closing
