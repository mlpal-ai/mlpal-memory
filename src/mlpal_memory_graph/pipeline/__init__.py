"""The incremental update pipeline: route -> extract -> embed -> resolve -> bi-temporal write."""

from .updater import Updater

__all__ = ["Updater"]
