"""What the adapter does with a reply that is not a clean JSON object.

Observed on migration-specification, the largest artefact any agent returns:

    artefact_unavailable:agent/migration-specification/authoring:3_attempts:
    model_response_schema_invalid:(root): {'answer': '```json\\n{\\n "specification": {...

Three full-size attempts, each reported as the model returning the wrong shape.
The reply was a document that did not parse as a whole. ``_parse_json`` then
fell through to its last candidate, the first ``[...]`` span -- an inner array
of that document -- which *did* parse, came back as a list, and so was neither
``None`` (which would have raised the truncation / unparseable errors) nor a
dict (so it was wrapped as ``{'answer': <raw text>}`` and handed to the schema).
"""
from types import SimpleNamespace

import pytest

from app.providers.cvscode_adapter import CvscodeClaudeAdapter, _parse_json

DOCUMENT = ('```json\n{\n "specification": {\n "metadata": {"service": "X"},\n'
            ' "interfaces": ["a", "b"],\n "mappings": [{"id": 1}')  # cut off mid-document


def _adapter(text, stop_reason):
    adapter = CvscodeClaudeAdapter()
    reply = SimpleNamespace(stop_reason=stop_reason,
                            content=[SimpleNamespace(type='text', text=text)])
    adapter._client = lambda: SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: reply))
    return adapter


def test_an_inner_array_is_not_mistaken_for_the_answer():
    assert _parse_json(DOCUMENT) is None


def test_a_cut_off_document_is_reported_as_truncated_not_as_a_bad_shape():
    with pytest.raises(RuntimeError) as raised:
        _adapter(DOCUMENT, 'max_tokens').invoke('t', {})
    assert str(raised.value).startswith('provider_response_truncated:')
    assert "Ended:" in str(raised.value), "the tail of the reply is kept for diagnosis"


def test_a_malformed_document_that_was_not_cut_off_is_reported_as_unparseable():
    with pytest.raises(RuntimeError) as raised:
        _adapter(DOCUMENT, 'end_turn').invoke('t', {})
    assert str(raised.value).startswith('provider_response_unparseable:')


def test_a_complete_fenced_document_still_parses():
    text = '```json\n{"specification": {"interfaces": ["a", "b"]}}\n```'
    assert _adapter(text, 'end_turn').invoke('t', {})['specification']['interfaces'] == ['a', 'b']


def test_prose_around_an_object_still_parses():
    assert _parse_json('Here it is: {"a": [1, 2]} -- done')['a'] == [1, 2]


def test_a_top_level_array_is_still_recoverable():
    assert _parse_json('Result:\n[{"a": 1}, {"a": 2}]') == [{'a': 1}, {'a': 2}]


def test_a_truncated_reply_says_where_the_token_budget_went():
    """39,313 characters against 32,768 tokens is ~1.2 chars/token: not all visible.

    The error has to separate reasoning from the visible answer, or the next
    person guesses at the cause the way this one did.
    """
    reply = SimpleNamespace(
        stop_reason='max_tokens', usage=SimpleNamespace(output_tokens=32768),
        content=[SimpleNamespace(type='thinking', thinking='t' * 5000, text=''),
                 SimpleNamespace(type='text', text=DOCUMENT)])
    adapter = CvscodeClaudeAdapter()
    adapter._client = lambda: SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: reply))
    with pytest.raises(RuntimeError) as raised:
        adapter.invoke('t', {})
    message = str(raised.value)
    assert 'output_tokens=32768' in message
    assert 'thinking:5000' in message and 'text:%d' % len(DOCUMENT) in message


# ---- reasoning effort ----------------------------------------------------------------------

def test_effort_defaults_to_the_aliases_level_and_can_be_lowered_per_call():
    """The coding alias runs at xhigh; a file listing does not need it."""
    adapter = CvscodeClaudeAdapter(alias='coding')
    assert adapter._request('t', {})['output_config'] == {'effort': 'xhigh'}
    assert adapter._request('t', {}, effort='low')['output_config'] == {'effort': 'low'}


def test_a_model_without_an_effort_parameter_never_gets_one():
    """Haiku errors on output_config.effort, so asking for one must be ignored."""
    adapter = CvscodeClaudeAdapter(alias='reasoning-small')
    assert adapter.effort is None
    assert 'output_config' not in adapter._request('t', {}, effort='low')


def test_the_effort_asked_for_reaches_the_request():
    sent = {}
    adapter = CvscodeClaudeAdapter(alias='coding')
    reply = SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text='{"a": 1}')])

    def create(**kwargs):
        sent.update(kwargs)
        return reply

    adapter._client = lambda: SimpleNamespace(messages=SimpleNamespace(create=create))
    adapter.invoke('t', {}, effort='medium')
    assert sent['output_config'] == {'effort': 'medium'}


def test_an_empty_thinking_block_is_reported_as_reasoning_not_as_none():
    """`thinking:0` was misread as "the model did not reason". It means the text is omitted."""
    reply = SimpleNamespace(
        stop_reason='max_tokens', usage=SimpleNamespace(output_tokens=32768),
        content=[SimpleNamespace(type='thinking', thinking='', text=''),
                 SimpleNamespace(type='text', text=DOCUMENT)])
    adapter = CvscodeClaudeAdapter()
    adapter._client = lambda: SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: reply))
    with pytest.raises(RuntimeError) as raised:
        adapter.invoke('t', {})
    assert 'reasoned before answering' in str(raised.value)
