"""KiloWattChiaro deterministic solar-evaluation engine.

The exact engine running in production at https://kilowattchiaro.it —
same inputs, same answer, every assumption a named setting.
"""

from .solar_eval import evaluate_solar

__version__ = "0.1.0"
__all__ = ["evaluate_solar", "__version__"]
