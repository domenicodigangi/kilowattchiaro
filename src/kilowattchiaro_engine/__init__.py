"""KiloWattChiaro deterministic solar-evaluation engine.

The engine running in production at https://kilowattchiaro.it: same inputs,
same answer. ``ENGINE_VERSION`` is the version every evaluation result carries
(``engine_version``) and the one /metodologia shows; the package version
follows it.
"""

from .solar_eval import evaluate_solar
from .version import ENGINE_VERSION

__version__ = "1.1.1"
__all__ = ["ENGINE_VERSION", "evaluate_solar", "__version__"]
