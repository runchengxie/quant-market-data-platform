"""Stable TuShare minute-ingest entry points.

The implementation remains behind the legacy provider shell during this staged
migration.  Keeping this import surface in ``ingest`` lets callers move first;
the helper modules can be relocated without another public API change.
"""

from market_data_platform.providers.tushare_a_share_mins import *  # noqa: F403
