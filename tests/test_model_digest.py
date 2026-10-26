import json
from app.agents import model_digest as md


def _model(n_esql=50, n_amb=300):
    return {'ambiguities': [{'question': 'q' * 400} for _ in range(n_amb)],
            'esql': [{'path': 'a%d.esql' % i, 'assignments': [{'esql': 'SET x = %d;' % j * 5} for j in range(200)]}
                     for i in range(n_esql)],
            'schemas': [{'name': 's', 'complexTypes': [{'name': 't%d' % k, 'body': 'x' * 100} for k in range(100)]}],
            'interfaces': [{'node': 'n'}]}


def test_digest_replaces_bulk_and_flags_itself():
    m = _model()
    d = md.shrink({'semantic_model': m}, 0)['semantic_model']
    assert d['ambiguities']['count'] == 300
    assert d['esql'][0]['assignment_count'] == 200
    assert len(d['esql'][0]['assignments']) == md.ASSIGNMENT_SAMPLE
    assert d['schemas'][0]['complexTypes'][0] == 't0'
    assert 'model_digest' in d
    assert len(json.dumps(d)) < len(json.dumps(m)) / 5


def test_levels_only_get_smaller_and_never_mutate_input():
    m = _model()
    before = json.dumps(m)
    sizes = [len(json.dumps(md.shrink({'semantic_model': m, 'tasks': [{'a': 'b'}] * 100}, l)))
             for l in range(md.LEVELS)]
    assert sizes == sorted(sizes, reverse=True)
    assert json.dumps(m) == before


def test_source_text_is_never_touched():
    p = {'text': 'x' * 1000, 'path': 'a.esql'}
    assert md.shrink(p, md.LEVELS - 1) == p


def test_ace_analysis_splits_an_oversized_file_at_line_boundaries_and_leaves_small_ones_whole():
    from app.agents.ace_analysis import ACEAnalysisAgent
    big = 'SET x = "a";\n' * 40000
    parts = ACEAnalysisAgent._chunks(big)
    assert len(parts) > 1 and ''.join(parts) == big
    assert all(len(json.dumps(p)) <= ACEAnalysisAgent.CHUNK_CHARS for p in parts)
    assert ACEAnalysisAgent._chunks('short') == ['short']


def test_migration_specification_digests_only_a_model_that_does_not_fit():
    from app.agents.migration_specification import MigrationSpecificationAgent
    agent = MigrationSpecificationAgent.__new__(MigrationSpecificationAgent)
    small = {'esql': [], 'ambiguities': []}
    assert agent._fit(small) is small
    fitted = agent._fit(_model())
    assert len(json.dumps(fitted)) <= MigrationSpecificationAgent.MODEL_BUDGET
