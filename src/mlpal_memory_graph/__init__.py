"""mlpal-memory-graph — institutional memory graph for enterprises using MLPal."""

from importlib.metadata import PackageNotFoundError, version as _dist_version


def _version() -> str:
    """The installed distribution's version: the private package or its public name."""
    for name in ("mlpal-memory-graph", "mlpal-memory"):
        try:
            return _dist_version(name)
        except PackageNotFoundError:
            continue
    return "0.0.0+unknown"


__version__ = _version()
