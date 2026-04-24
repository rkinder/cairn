import re

from cairn.config import get_settings

_RE_NUMBERED = re.compile(r"^\d+[\.)]\s+(.+)$", re.MULTILINE)
_RE_BULLETED = re.compile(r"^[-*\u2022]\s+(.+)$", re.MULTILINE)
_MIN_STEP_LEN = 10


def _filter_short(steps: list[str]) -> list[str]:
    return [s.strip() for s in steps if s and len(s.strip()) >= _MIN_STEP_LEN]


def _spacy_sentences(text: str) -> list[str]:
    settings = get_settings()
    if not settings.spacy_enabled:
        return []

    try:
        import spacy
    except Exception:
        return []

    try:
        nlp = spacy.blank("en")
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer")
        doc = nlp(text)
        return [s.text.strip() for s in doc.sents if s.text and s.text.strip()]
    except Exception:
        return []


def extract_steps(text: str) -> list[str]:
    if not text or not text.strip():
        return []

    numbered = _RE_NUMBERED.findall(text)
    if numbered:
        return _filter_short(numbered)

    bulleted = _RE_BULLETED.findall(text)
    if bulleted:
        return _filter_short(bulleted)

    spacy_sentences = _spacy_sentences(text)
    if spacy_sentences:
        return _filter_short(spacy_sentences)

    sentences = [s.strip() for s in text.split(". ")]
    return _filter_short(sentences)
