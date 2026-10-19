"""Model requests that do not depend on each other run concurrently.

ace-analysis sends one request per source file and took half an hour doing it one
at a time. The requests are independent, so the only things that must survive
the change are the ones the sequential loop guaranteed: results in the order
given, all-or-nothing failure, and a stop request that still lands promptly.
"""
import threading
import time
import types

import pytest

from app.agents import artefact
from app.agents.artefact import ArtefactUnavailable, StepCancelled, run_parallel


def _ctx(stopping=lambda: False):
    return types.SimpleNamespace(repo=None, cancelled=stopping, job_id='j', agent_id='a')


@pytest.fixture(autouse=True)
def _four(monkeypatch):
    monkeypatch.setenv('MOJO_LLM_CONCURRENCY', '4')
    monkeypatch.setattr(artefact, '_log', lambda c: (lambda *a: None, lambda *a: None))


def test_calls_really_overlap():
    """A barrier that needs all four in flight at once cannot pass sequentially."""
    barrier = threading.Barrier(4, timeout=5)

    def call(i):
        return lambda: (barrier.wait(), i)[1]

    assert run_parallel(_ctx(), 'p', [call(i) for i in range(4)]) == [0, 1, 2, 3]


def test_results_keep_the_order_given_whatever_finishes_first():
    def call(i):
        def go():
            time.sleep(0.2 - i * 0.05)          # the last one finishes first
            return i
        return go

    assert run_parallel(_ctx(), 'p', [call(i) for i in range(4)]) == [0, 1, 2, 3]


def test_concurrency_of_one_is_strictly_sequential(monkeypatch):
    monkeypatch.setenv('MOJO_LLM_CONCURRENCY', '1')
    order = []
    run_parallel(_ctx(), 'p', [lambda i=i: order.append(i) for i in range(5)])
    assert order == [0, 1, 2, 3, 4]


def test_a_failure_raises_and_calls_not_yet_started_are_dropped(monkeypatch):
    monkeypatch.setenv('MOJO_LLM_CONCURRENCY', '2')
    started = []

    def ok(i):
        def go():
            started.append(i)
            time.sleep(0.05)
            return i
        return go

    def bad():
        started.append('bad')
        raise ArtefactUnavailable('p', 'model_response_schema_invalid', 3, {})

    calls = [bad] + [ok(i) for i in range(20)]
    with pytest.raises(ArtefactUnavailable):
        run_parallel(_ctx(), 'p', calls)
    assert len(started) < 21, 'the rest of the queue was not spent after the failure'


def test_the_first_failure_by_input_order_is_the_one_raised():
    def boom(msg):
        def go():
            raise ArtefactUnavailable('p', msg, 1, {})
        return go

    with pytest.raises(ArtefactUnavailable) as raised:
        run_parallel(_ctx(), 'p', [lambda: 1, boom('first'), boom('second')])
    assert raised.value.reason == 'first'


def test_a_stop_request_cancels_before_the_next_call_starts(monkeypatch):
    monkeypatch.setenv('MOJO_LLM_CONCURRENCY', '2')
    stop = threading.Event()
    ran = []

    def call(i):
        def go():
            ran.append(i)
            stop.set()
            time.sleep(0.05)
            return i
        return go

    with pytest.raises(StepCancelled):
        run_parallel(_ctx(stop.is_set), 'p', [call(i) for i in range(10)])
    assert len(ran) < 10


def test_no_calls_is_an_empty_result():
    assert run_parallel(_ctx(), 'p', []) == []
