"""Shrinking an oversized request payload instead of refusing it.

``artefact.produce`` refuses any payload over ``MAX_PAYLOAD_CHARS`` because no
retry makes a request smaller. For source text the answer is to split it (see
``ace_analysis``). For the *semantic model* and the plan's tasks, which every
later agent sends along as context, splitting does not apply: the model needs the
whole picture, just not every ESQL assignment and schema type in it.

A real service's semantic model came to 403,881 characters -- 124,087 of them
ambiguities, 112,986 of ESQL assignments and 64,086 of schema complex types. Sent
whole to each request that is over the limit before the model is reached.

A payload that fits is never touched. Otherwise ``shrink`` is applied in
increasing levels, each lossier than the last, and the caller stops at the first
that fits. The digest says it is one (``model_digest``), so a reply is not mistaken
for a full read.
"""
import json

#: Assignments kept per ESQL module at the first level; the total is recorded.
ASSIGNMENT_SAMPLE = 8

#: List lengths kept at the later levels, in turn.
LIST_CAPS = (20, 10, 5, 3)

#: The bulky areas of a semantic model the later levels sample.
MODEL_AREAS = ('esql', 'schemas', 'subflows', 'dependencies', 'maps', 'behaviour',
               'interfaces', 'flows')

#: Payload keys that carry context rather than the thing being authored, and so
#: may be sampled. Source ``text``, schemas and corrections are never touched.
SHRINKABLE = ('semantic_model', 'tasks', 'framework_standards', 'specification')

#: How many levels ``shrink`` has.
LEVELS = 1 + len(LIST_CAPS)


def cap_lists(value, cap):
    """``value`` with every list longer than ``cap`` cut, and ``<key>_count`` added."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out[key] = cap_lists(item, cap)
            if isinstance(item, list) and len(item) > cap:
                out[key + '_count'] = len(item)
        return out
    if isinstance(value, list):
        return [cap_lists(item, cap) for item in value[:cap]]
    return value


def _esql_digest(module):
    module = dict(module or {})
    assignments = module.get('assignments') or []
    if len(assignments) > ASSIGNMENT_SAMPLE:
        module['assignments'] = assignments[:ASSIGNMENT_SAMPLE]
        module['assignment_count'] = len(assignments)
    return module


def _schema_digest(schema):
    schema = dict(schema or {})
    types = schema.get('complexTypes')
    if isinstance(types, (list, dict)) and len(json.dumps(types)) > 2000:
        names = ([t.get('name') if isinstance(t, dict) else t for t in types]
                 if isinstance(types, list) else list(types))
        schema['complexTypes'] = names
        schema['complexType_count'] = len(names)
    return schema


def digest_model(model):
    """A semantic model with the bulk replaced by names, counts and samples."""
    if not isinstance(model, dict):
        return model
    out = dict(model)
    ambiguities = model.get('ambiguities')
    if isinstance(ambiguities, list):
        out['ambiguities'] = {'count': len(ambiguities),
                              'note': 'recorded on the analysis; not repeated here'}
    if isinstance(model.get('esql'), list):
        out['esql'] = [_esql_digest(e) for e in model['esql']]
    if isinstance(model.get('schemas'), list):
        out['schemas'] = [_schema_digest(x) for x in model['schemas']]
    out['model_digest'] = ('Large inputs are summarised: ESQL assignments are sampled '
                           'and schema complex types are listed by name.')
    return out


def shrink(payload, level):
    """``payload`` reduced to ``level`` (0 .. ``LEVELS - 1``). Never mutates it."""
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    if isinstance(out.get('semantic_model'), dict):
        out['semantic_model'] = digest_model(out['semantic_model'])
    for cap in LIST_CAPS[:max(0, level)]:
        for key in SHRINKABLE:
            if key not in out:
                continue
            value = out[key]
            if key == 'semantic_model' and isinstance(value, dict):
                value = {k: (cap_lists(v, cap) if k in MODEL_AREAS else v) for k, v in value.items()}
                value['model_digest'] = (str(value.get('model_digest', ''))
                                         + ' Lists are sampled to %d entries; *_count gives the total.' % cap)
            else:
                value = cap_lists(value, cap)
            out[key] = value
    return out
