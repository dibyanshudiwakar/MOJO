"""Test package marker.

The agent tests import shared fixtures with ``from tests.agents.conftest import
ALL_ROLES``, which needs ``tests`` and ``tests.agents`` to be importable
packages. Without these markers the whole tests/agents directory failed at
collection with ModuleNotFoundError.
"""
