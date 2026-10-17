"""Model provider adapters and the process-wide default."""
import importlib
import os

_cached = {}


def default_provider():
    """The provider every agent uses when the caller supplies none.

    ``AI_PROVIDER`` selects it:

    * ``gateway`` -- the CVS cvscode gateway (``start.ps1`` sets this).
    * ``<module>:<attribute>`` -- any other ``ProviderExecutionAdapter``,
      resolved by import path and called with no arguments. This is the seam
      ``ProviderExecutionAdapter`` exists for: an enterprise plugs in its own
      adapter without this package having to know about it. A bad path raises
      rather than returning None, because a misconfigured provider now stops
      every agent and must say so at startup instead of looking like an outage.
    * anything else, or unset -- None.

    None used to mean "agents run their deterministic fallback". It no longer
    means anything so forgiving: ``artefact.produce`` raises
    ``ArtefactUnavailable`` and the agent fails. A deployment with no provider
    configured produces nothing at all, which is the intended behaviour -- a
    factory that cannot reach Claude is not a factory that quietly computes.
    """
    name = (os.environ.get('AI_PROVIDER') or '').strip()
    if not name or name.lower() == 'none':
        return None
    if name in _cached:
        return _cached[name]
    if name.lower() == 'gateway':
        from app.providers.cvscode_adapter import CvscodeClaudeAdapter
        _cached[name] = CvscodeClaudeAdapter()
        return _cached[name]
    if ':' not in name:
        return None
    module, _, attribute = name.partition(':')
    factory = getattr(importlib.import_module(module), attribute)
    _cached[name] = factory()
    return _cached[name]
