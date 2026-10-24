"""Small process-local cache. No ledger or user data is written to disk."""
from concurrent.futures import Future
from threading import Lock
from time import monotonic


def unwrap_database(scope):
    """Unwrap _SessionDatabase wrappers without triggering PyMongo Database.__getattr__('db')."""
    while type(scope).__name__ == "_SessionDatabase":
        scope = scope.__dict__.get("db", scope)
    return scope


class ReferenceCache:
    def __init__(self, ttl_seconds=300, dependents=()):
        self.ttl_seconds = ttl_seconds
        self._lock = Lock()
        self._generation = 0
        self._values = {}
        self._pending = {}
        self._dependents = list(dependents)

    def invalidate(self):
        # Never wait for a database read while running in ASGI middleware.
        with self._lock:
            self._generation += 1
            self._values.clear()
            self._pending.clear()
        for dependent in self._dependents:
            dependent.invalidate()

    def get(self, scope, build):
        raw_scope = unwrap_database(scope)
        key = id(raw_scope)
        while True:
            with self._lock:
                generation = self._generation
                now = monotonic()
                cached = self._values.get(key)
                if cached and cached[0] is raw_scope and cached[1] > now:
                    return cached[2]
                catalog_cached = self._values.get("catalog") if not isinstance(scope, str) else None
                if catalog_cached and catalog_cached[1] > now:
                    return catalog_cached[2]
                future = self._pending.get((key, generation))
                owner = future is None
                if owner:
                    future = Future()
                    self._pending[(key, generation)] = future
            if owner:
                try:
                    value = build()
                    with self._lock:
                        if generation == self._generation:
                            if len(self._values) >= 16:
                                self._values.pop(next(iter(self._values)))
                            self._values[key] = (raw_scope, monotonic() + self.ttl_seconds, value)
                    future.set_result(value)
                except BaseException as error:
                    future.set_exception(error)
                finally:
                    with self._lock:
                        self._pending.pop((key, generation), None)
            value = future.result()
            with self._lock:
                if generation == self._generation:
                    return value

    def set(self, scope, value):
        raw_scope = unwrap_database(scope)
        key = scope if isinstance(scope, str) else id(raw_scope)
        with self._lock:
            if len(self._values) >= 16 and key not in self._values:
                self._values.pop(next(iter(self._values)))
            self._values[key] = (raw_scope, monotonic() + self.ttl_seconds, value)
        return value


historical_context_cache = ReferenceCache(ttl_seconds=120)
subcategory_catalog_cache = ReferenceCache(ttl_seconds=300)
statement_review_cache = ReferenceCache(ttl_seconds=120)
dashboard_snapshot_cache = ReferenceCache(ttl_seconds=30, dependents=(statement_review_cache,))
classification_cache = ReferenceCache(
    ttl_seconds=300,
    dependents=(historical_context_cache, subcategory_catalog_cache, dashboard_snapshot_cache, statement_review_cache),
)
