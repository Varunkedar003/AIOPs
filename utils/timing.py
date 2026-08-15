"""Lightweight timing instrumentation for Azure discovery/query calls.

Every Azure Resource Graph / Monitor / Alerts / Cost Management / Log Analytics / AKS
call in providers/azure/ goes through one of these two helpers so slow calls show up in
the logs with a consistent "[azure-timing] <label> took <n>ms" line - the goal is being
able to answer "which Azure query is responsible for the delay" from the logs alone.
"""
import functools
import logging
import time
from contextlib import contextmanager


@contextmanager
def log_timing(logger: logging.Logger, label: str):
    """Context manager form, for wrapping a single network call inline (use when a method
    makes more than one Azure call and each needs its own timing line)."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("[azure-timing] %s took %.0fms", label, elapsed_ms)


def log_azure_call(logger: logging.Logger):
    """Decorator form, for a method that makes exactly one Azure call - logs
    "<ClassName>.<method_name>" as the label automatically."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            label = f"{type(self).__name__}.{func.__name__}"
            start = time.perf_counter()
            try:
                return func(self, *args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.info("[azure-timing] %s took %.0fms", label, elapsed_ms)
        return wrapper
    return decorator
