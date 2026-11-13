"""Version of the /solar economics engine.

Bump it, and add an entry to ``docs/engine-changelog.md``, whenever a change
moves any number a user sees. Every evaluation result carries it. The golden
snapshots (``tests/test_golden_snapshots.py``) fail until both are done. /metodologia
shows it from ``frontend/src/data/assumptions.json``: rerun
``scripts/export_assumptions.py`` after a bump (a test fails until you do).
"""

ENGINE_VERSION = "1.1.1"
