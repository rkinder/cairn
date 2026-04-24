import pytest

from cairn.nlp.step_extractor import extract_steps


def test_numbered_list_dot():
    text = "1. First valid step here\n2. Second valid step here"
    assert extract_steps(text) == ["First valid step here", "Second valid step here"]


def test_numbered_list_paren():
    text = "1) First valid step here\n2) Second valid step here"
    assert extract_steps(text) == ["First valid step here", "Second valid step here"]


def test_bulleted_dash():
    text = "- First valid step here\n- Second valid step here"
    assert extract_steps(text) == ["First valid step here", "Second valid step here"]


def test_bulleted_star():
    text = "* First valid step here\n* Second valid step here"
    assert extract_steps(text) == ["First valid step here", "Second valid step here"]


def test_bulleted_unicode():
    text = "• First valid step here\n• Second valid step here"
    assert extract_steps(text) == ["First valid step here", "Second valid step here"]


def test_sentence_split_fallback():
    text = "Collect mailbox logs for user. Correlate sender infrastructure with known IOCs."
    assert extract_steps(text) == [
        "Collect mailbox logs for user",
        "Correlate sender infrastructure with known IOCs.",
    ]


def test_short_steps_filtered():
    text = "1. short\n2. This is long enough"
    assert extract_steps(text) == ["This is long enough"]


def test_numbered_priority_over_bulleted():
    text = "1. Numbered step one here\n2. Numbered step two here\n- Bulleted fallback step"
    assert extract_steps(text) == ["Numbered step one here", "Numbered step two here"]


def test_empty_string_returns_empty():
    assert extract_steps("") == []


def test_whitespace_only_returns_empty():
    assert extract_steps("   ") == []


def test_single_sentence_returns_list():
    text = "This single sentence is long enough"
    assert extract_steps(text) == ["This single sentence is long enough"]
