"""Every clarification has a suggestion: the platform standard when nothing better exists."""
from app.services import standards_suggestion as std


def test_a_question_always_gets_an_answer():
    for question in ('What is the context root?', '', None, 'zzz qqq'):
        got = std.baseline(question)
        assert got['suggested_answer'].strip()
        assert got['suggestion_mode'] == 'STANDARDS_BASELINE'


def test_it_is_never_confident_enough_for_auto_mode():
    assert std.baseline('Which password should be used?')['suggestion_confidence'] == 'LOW'


def test_the_answer_follows_the_kind_of_question():
    assert 'environment' in std.baseline('Where does the database password come from?')['suggested_answer']
    assert 'dead-letter' in std.baseline('How are failed MQ messages handled?')['suggested_answer']
    assert std.baseline('unrelated words')['suggested_answer'] == std.DEFAULT
