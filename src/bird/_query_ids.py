"""Runtime query ID store — fetches and caches X/Twitter GraphQL query IDs.

X rotates these IDs frequently; this module scrapes them from the public
x.com JavaScript bundles so the client stays functional without manual updates.
Cache lives at ~/.config/bird/query-ids-cache.json (24 h TTL by default).
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import httpx

from ._constants import FALLBACK_QUERY_IDS

_DEFAULT_TTL = 24 * 60 * 60  # seconds
_CACHE_FILENAME = "query-ids-cache.json"
_DISCOVERY_PAGES = [
    "https://x.com/?lang=en",
    "https://x.com/explore",
    "https://x.com/notifications",
    "https://x.com/settings/profile",
]
_BUNDLE_URL_RE = re.compile(
    r"https://abs\.twimg\.com/responsive-web/client-web(?:-legacy)?/[A-Za-z0-9.\-]+\.js"
)
_QUERY_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_OPERATION_PATTERNS: list[tuple[re.Pattern, int, int]] = [
    # (pattern, operation_group, query_id_group)
    (re.compile(r'e\.exports=\{queryId\s*:\s*["\']([^"\']+)["\']\s*,\s*operationName\s*:\s*["\']([^"\']+)["\']'), 2, 1),
    (re.compile(r'e\.exports=\{operationName\s*:\s*["\']([^"\']+)["\']\s*,\s*queryId\s*:\s*["\']([^"\']+)["\']'), 1, 2),
    (re.compile(r'operationName\s*[:=]\s*["\']([^"\']+)["\'].{0,4000}?queryId\s*[:=]\s*["\']([^"\']+)["\']', re.DOTALL), 1, 2),
    (re.compile(r'queryId\s*[:=]\s*["\']([^"\']+)["\'].{0,4000}?operationName\s*[:=]\s*["\']([^"\']+)["\']', re.DOTALL), 2, 1),
]
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _default_cache_path() -> Path:
    import os
    override = os.environ.get("BIRD_QUERY_IDS_CACHE", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".config" / "bird" / _CACHE_FILENAME


def _load_cache(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("fetchedAt", "ttl", "ids"):
        if key not in data:
            return None
    if not isinstance(data["ids"], dict):
        return None
    return data


def _save_cache(path: Path, snapshot: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2) + "\n")


def _discover_bundles(client: httpx.Client) -> list[str]:
    bundles: set[str] = set()
    for page in _DISCOVERY_PAGES:
        try:
            r = client.get(page, headers=_FETCH_HEADERS, timeout=20)
            if r.is_success:
                bundles.update(_BUNDLE_URL_RE.findall(r.text))
        except Exception:
            pass
    if not bundles:
        raise RuntimeError("No X/Twitter client bundles discovered; layout may have changed.")
    return list(bundles)


def _extract_operations(js: str, targets: set[str], discovered: dict[str, str]) -> None:
    for pattern, op_group, qid_group in _OPERATION_PATTERNS:
        for m in pattern.finditer(js):
            op_name = m.group(op_group)
            query_id = m.group(qid_group)
            if not op_name or not query_id:
                continue
            if op_name not in targets or op_name in discovered:
                continue
            if not _QUERY_ID_RE.match(query_id):
                continue
            discovered[op_name] = query_id
            if len(discovered) == len(targets):
                return


def _fetch_and_extract(
    client: httpx.Client, bundle_urls: list[str], targets: set[str]
) -> dict[str, str]:
    discovered: dict[str, str] = {}
    CONCURRENCY = 6

    def fetch_one(url: str) -> Optional[str]:
        try:
            r = client.get(url, headers=_FETCH_HEADERS, timeout=30)
            return r.text if r.is_success else None
        except Exception:
            return None

    for i in range(0, len(bundle_urls), CONCURRENCY):
        if len(discovered) == len(targets):
            break
        chunk = bundle_urls[i : i + CONCURRENCY]
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            futures = {ex.submit(fetch_one, url): url for url in chunk}
            for fut in as_completed(futures):
                js = fut.result()
                if js:
                    _extract_operations(js, targets, discovered)
                if len(discovered) == len(targets):
                    break
    return discovered


class QueryIdStore:
    """Thread-safe, disk-backed cache for X/Twitter GraphQL query IDs."""

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        ttl: int = _DEFAULT_TTL,
    ) -> None:
        self._cache_path = cache_path or _default_cache_path()
        self._ttl = ttl
        self._snapshot: Optional[dict] = None

    def _load(self) -> None:
        if self._snapshot is None:
            self._snapshot = _load_cache(self._cache_path)

    def _is_fresh(self) -> bool:
        if not self._snapshot:
            return False
        try:
            age = time.time() - self._snapshot["fetchedAt"]
            return age <= self._snapshot.get("ttl", self._ttl)
        except Exception:
            return False

    def get(self, operation: str) -> str:
        self._load()
        if self._snapshot:
            cached = self._snapshot.get("ids", {}).get(operation)
            if cached:
                return cached
        return FALLBACK_QUERY_IDS.get(operation, "")

    def refresh(self, operations: list[str], force: bool = False) -> None:
        self._load()
        if not force and self._is_fresh():
            return
        targets = set(operations)
        try:
            with httpx.Client() as client:
                bundle_urls = _discover_bundles(client)
                found = _fetch_and_extract(client, bundle_urls, targets)
        except Exception:
            return  # silently keep using fallbacks
        if not found:
            return
        snapshot = {
            "fetchedAt": time.time(),
            "ttl": self._ttl,
            "ids": found,
        }
        try:
            _save_cache(self._cache_path, snapshot)
        except Exception:
            pass
        self._snapshot = snapshot

    def info(self) -> dict:
        self._load()
        if not self._snapshot:
            return {"cached": False, "cachePath": str(self._cache_path)}
        age = time.time() - self._snapshot.get("fetchedAt", 0)
        return {
            "cached": True,
            "cachePath": str(self._cache_path),
            "fetchedAt": self._snapshot.get("fetchedAt"),
            "ttl": self._snapshot.get("ttl", self._ttl),
            "ageSeconds": int(age),
            "fresh": self._is_fresh(),
            "ids": self._snapshot.get("ids", {}),
        }


# Module-level singleton — shared across all TwitterClient instances
query_id_store = QueryIdStore()
