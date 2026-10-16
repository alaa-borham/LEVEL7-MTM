"""LEVEL7 MTM — made-to-measure pattern generation from ERP orders."""
__base_version__ = "0.1"
try:                                    # written by the Windows build (CI run number); absent in a dev checkout
    from ._build import BUILD
except ImportError:                     # pragma: no cover
    BUILD = 0
__version__ = f"{__base_version__}.{BUILD}"
ENGINE_VERSION = "1.0"     # changes whenever geometry results for the same inputs could change
