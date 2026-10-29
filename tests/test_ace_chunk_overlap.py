"""A part of a split file carries the end of the part before it, and a construct it repeats is kept once."""
import json

from app.agents import ace_analysis as A


def test_the_tail_is_the_last_whole_lines_within_budget():
    previous = ''.join('line %d\n' % i for i in range(20000))
    tail = A.tail_of(previous)
    assert previous.endswith(tail) and tail.startswith('line ')
    assert len(json.dumps(tail)) <= A.TAIL_CHARS
    assert tail.endswith('line 19999\n')


def test_a_part_and_its_tail_together_fit_one_request():
    big = 'SET x = "a";\n' * 40000
    parts = A.ACEAnalysisAgent._chunks(big)
    assert len(parts) > 1
    for previous, part in zip(parts, parts[1:]):
        tail = A.tail_of(previous)
        assert len(json.dumps(part)) + len(json.dumps(tail)) <= A.ACEAnalysisAgent.CHUNK_CHARS


def test_a_construct_repeated_by_a_later_part_of_the_same_file_is_kept_once():
    first = {'flows': [{'name': 'orders', 'nodes': ['in']}], 'esql': []}
    again = {'flows': [{'name': 'orders', 'nodes': ['in', 'out']}, {'name': 'refunds', 'nodes': []}]}
    kept = A.dedupe_parts([('orders.msgflow#part1', first), ('orders.msgflow#part2', again)])
    names = [f['name'] for _, frag in kept for f in frag['flows']]
    assert names == ['orders', 'refunds']


def test_the_same_name_in_a_different_file_is_a_different_construct():
    a = {'flows': [{'name': 'main', 'nodes': []}]}
    b = {'flows': [{'name': 'main', 'nodes': []}]}
    kept = A.dedupe_parts([('a.msgflow', a), ('b.msgflow#part1', b)])
    assert sum(len(frag['flows']) for _, frag in kept) == 2
