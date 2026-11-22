"""Reverse geocoding with OpenStreetMap Nominatim.

Follows the public usage policy (https://operations.osmfoundation.org/policies/nominatim/):
  * identifying User-Agent, never the library default
  * at most one request per second
  * results are cached (on disk) so the same place is never requested twice
  * stops after repeated failures or HTTP 403/429 instead of hammering the server
Only rounded coordinates are sent (about 1 km), which also keeps the request count low.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
PLACE_KEYS = (
    "city", "town", "village", "municipality", "hamlet", "suburb",
    "city_district", "county", "state_district", "state",
)
ATTRIBUTION = "Place names © OpenStreetMap contributors (ODbL), via Nominatim."

STATUS_OK = "ok"
STATUS_NO_RESULT = "no_result"
STATUS_FAILED = "failed"


@dataclass
class GeocodeResult:
    status: str
    name: Optional[str] = None
    message: str = ""
    from_cache: bool = False


def _default_fetch(url: str, headers: Dict[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class NominatimGeocoder:
    def __init__(
        self,
        cache_path: Optional[Path] = None,
        contact: str = "",
        language: str = "",
        precision: int = 2,
        min_interval: float = 1.1,
        timeout: float = 10.0,
        max_consecutive_failures: int = 3,
        fetch: Callable[[str, Dict[str, str], float], bytes] = _default_fetch,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cache_path = Path(cache_path) if cache_path else None
        self.language = language.strip()
        self.precision = precision
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_consecutive_failures = max_consecutive_failures
        self._fetch, self._sleep, self._clock = fetch, sleep, clock
        agent = "PhotoOrganiser/0.1 (personal photo-sorting tool"
        agent += f"; contact: {contact.strip()})" if contact.strip() else ")"
        self.user_agent = agent
        self.requests_made = 0
        self.disabled_reason = ""
        self._failures = 0
        self._last_request: Optional[float] = None
        self._cache: Dict[str, dict] = self._load_cache()

    # ---- cache -------------------------------------------------------------
    def cache_key(self, lat: float, lon: float) -> str:
        return f"{self.language}|{round(lat, self.precision):.{self.precision}f},{round(lon, self.precision):.{self.precision}f}"

    def _load_cache(self) -> Dict[str, dict]:
        if not self.cache_path or not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}  # unreadable cache: start fresh, it is only an optimisation

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError:
            pass

    # ---- lookup ------------------------------------------------------------
    def lookup(self, lat: float, lon: float) -> GeocodeResult:
        key = self.cache_key(lat, lon)
        if key in self._cache:
            entry = self._cache[key]
            return GeocodeResult(entry["status"], entry.get("name"), "cached", from_cache=True)
        if self.disabled_reason:
            return GeocodeResult(STATUS_FAILED, message=self.disabled_reason)

        self._respect_rate_limit()
        params = {
            "format": "jsonv2",
            "lat": f"{round(lat, self.precision):.{self.precision}f}",
            "lon": f"{round(lon, self.precision):.{self.precision}f}",
            "zoom": "10",  # town/city level
            "addressdetails": "1",
        }
        headers = {"User-Agent": self.user_agent}
        if self.language:
            headers["Accept-Language"] = self.language
        url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
        try:
            self.requests_made += 1
            body = self._fetch(url, headers, self.timeout)
            data = json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self._last_request = self._clock()
            if exc.code in (403, 429):
                self.disabled_reason = f"Geocoding service refused requests (HTTP {exc.code}); stopped to respect its usage policy."
                return GeocodeResult(STATUS_FAILED, message=self.disabled_reason)
            return self._failure(f"HTTP {exc.code}")
        except Exception as exc:  # network down, timeout, bad JSON ...
            self._last_request = self._clock()
            return self._failure(f"{type(exc).__name__}: {exc}")

        self._last_request = self._clock()
        self._failures = 0
        name = None
        if isinstance(data, dict) and "error" not in data:
            address = data.get("address") or {}
            name = next((address[k] for k in PLACE_KEYS if address.get(k)), None) or data.get("name") or None
        status = STATUS_OK if name else STATUS_NO_RESULT
        self._cache[key] = {"status": status, "name": name}
        self._save_cache()
        return GeocodeResult(status, name)

    def _failure(self, message: str) -> GeocodeResult:
        self._failures += 1
        if self._failures >= self.max_consecutive_failures:
            self.disabled_reason = (
                f"Geocoding paused after {self._failures} failures in a row (last: {message}). "
                "Remaining photos keep 'Unknown Location'; try again later."
            )
        return GeocodeResult(STATUS_FAILED, message=message)  # failures are never cached

    def _respect_rate_limit(self) -> None:
        if self._last_request is not None:
            wait = self.min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
