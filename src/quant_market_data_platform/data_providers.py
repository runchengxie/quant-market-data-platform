"""Provider-agnostic data access helpers for the A-share research workflow.

The implementation now lives in :mod:`data_providers_client` and this module keeps
backward-compatible imports for callers.
"""

from __future__ import annotations

from . import data_providers_core as _core

# Re-export the original implementation surface so imports remain unchanged.
__all__ = [name for name in vars(_core) if not name.startswith("__")]
for _name in __all__:
    globals()[_name] = getattr(_core, _name)
