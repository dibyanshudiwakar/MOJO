"""Model provider adapters and the process-wide default."""
import os

_cached = None


def default_provider():
    """The provider every agent uses when the caller supplies none.

    Opt-in via ``AI_PROVIDER=gateway`` (the CVS cvscode gateway; ``start.ps1``
    sets this). Unset or any other value returns None, which keeps agents on the
    deterministic path -- so tests and CI never call out to a model.
    """
    global _cached
    if (os.environ.get('AI_PROVIDER') or '').lower() != 'gateway':
        return None
    if _cached is None:
        from app.providers.cvscode_adapter import CvscodeClaudeAdapter
        _cached = CvscodeClaudeAdapter()
    return _cached
