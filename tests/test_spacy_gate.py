import pytest
import sys
from unittest.mock import patch, MagicMock

from cairn.nlp.step_extractor import extract_steps
from cairn.config import Settings

def test_spacy_enabled_via_config():
    text = "First sentence. Second sentence with no bullet."
    
    with patch("cairn.nlp.step_extractor.get_settings") as mock_settings:
        mock_settings.return_value = Settings(spacy_enabled=True)
        
        mock_spacy = MagicMock()
        mock_doc = MagicMock()
        mock_doc.sents = [MagicMock(text="First sentence."), MagicMock(text="Second sentence with no bullet.")]
        mock_nlp = MagicMock()
        mock_nlp.return_value = mock_doc
        mock_nlp.pipe_names = []
        mock_spacy.blank.return_value = mock_nlp

        with patch.dict(sys.modules, {"spacy": mock_spacy}):
            res = extract_steps(text)
            assert len(res) == 2
            mock_spacy.blank.assert_called_once_with("en")

def test_spacy_disabled_via_config():
    text = "First sentence. Second sentence with no bullet."
    
    with patch("cairn.nlp.step_extractor.get_settings") as mock_settings:
        mock_settings.return_value = Settings(spacy_enabled=False)
        mock_spacy = MagicMock()

        with patch.dict(sys.modules, {"spacy": mock_spacy}):
            res = extract_steps(text)
            assert len(res) == 2
            mock_spacy.blank.assert_not_called()
