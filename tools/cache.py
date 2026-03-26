# tools/cache.py

import json
import time
import hashlib
import redis

class ToolCache:
    def __init__(self, redis_url: str = "redis://localhost:6379", prefix: str = "csrag"):
        self._redis = redis.from_url(redis_url)
        self._prefix = prefix
        print(f"[CACHE] Connected to Redis at {redis_url}")

    def _make_key(self, tool_name: str, **params) -> str:
        rounded = {}
        for k, v in sorted(params.items()):
            if isinstance(v, float):
                rounded[k] = round(v, 3)
            elif v is None:
                continue
            else:
                rounded[k] = v
        raw = f"{tool_name}:{rounded}"
        return f"{self._prefix}:{hashlib.md5(raw.encode()).hexdigest()}"

    def get(self, tool_name: str, ttl_seconds: int = 1800, **params):
        key = self._make_key(tool_name, **params)
        data = self._redis.get(key)
        if data:
            print(f"[CACHE HIT] {tool_name} (key={key[-8:]})")
            return data.decode('utf-8')
        return None

    def set(self, tool_name: str, value, ttl_seconds: int = 1800, **params):
        key = self._make_key(tool_name, **params)
        if not isinstance(value, (str, bytes)):
            value = json.dumps(value, default=str)
        self._redis.setex(key, ttl_seconds, value)
        print(f"[CACHE SET] {tool_name} (key={key[-8:]}) ttl={ttl_seconds}s")

    def clear(self):
        keys = self._redis.keys(f"{self._prefix}:*")
        if keys:
            self._redis.delete(*keys)
        print(f"[CACHE] Cleared {len(keys)} entries")

    def stats(self) -> dict:
        keys = self._redis.keys(f"{self._prefix}:*")
        return {
            "entries": len(keys),
            "redis_info": self._redis.info("memory")["used_memory_human"],
        }

tool_cache = ToolCache()