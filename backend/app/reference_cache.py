"""Small process-local cache. No ledger or user data is written to disk."""
from concurrent.futures import Future
from threading import Lock
from time import monotonic


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
        raw_scope = getattr(scope, "db", scope)
        key = id(raw_scope)
        while True:
            with self._lock:
                generation = self._generation
                cached = self._values.get(key)
                if cached and cached[0] is raw_scope and cached[1] > monotonic():
                    return cached[2]
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


historical_context_cache = ReferenceCache(ttl_seconds=120)
subcategory_catalog_cache = ReferenceCache(ttl_seconds=300)
classification_cache = ReferenceCache(
    ttl_seconds=300,
    dependents=(historical_context_cache, subcategory_catalog_cache),
)
