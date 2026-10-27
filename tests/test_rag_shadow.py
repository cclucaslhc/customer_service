import json

import pytest

from scripts.compare_rag_shadow import load_questions


def test_shadow_questions_are_bounded(tmp_path):
    path = tmp_path / 'synthetic-questions.json'
    path.write_text(json.dumps(['Where is the synthetic order?']), encoding='utf-8')
    assert load_questions(path) == ['Where is the synthetic order?']
    path.write_text(json.dumps(['']), encoding='utf-8')
    with pytest.raises(ValueError, match='Questions'):
        load_questions(path)
    path.write_text(json.dumps(['x'] * 101), encoding='utf-8')
    with pytest.raises(ValueError, match='Questions'):
        load_questions(path)
