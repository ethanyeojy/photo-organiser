import json
import urllib.error

from photo_organiser.geocoder import NominatimGeocoder


class FakeFetch:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return json.dumps(item).encode()


def make(fetch, **kw):
    clock = {"t": 0.0}
    sleeps = []
    def sleep(s):
        sleeps.append(s)
        clock["t"] += s
    g = NominatimGeocoder(fetch=fetch, sleep=sleep, clock=lambda: clock["t"], **kw)
    g.sleeps = sleeps
    return g


def test_finds_town_sends_rounded_coords_and_identifying_agent():
    fetch = FakeFetch([{"address": {"town": "Paihia", "state": "Northland"}}])
    g = make(fetch, contact="me@example.com")
    r = g.lookup(-35.28123, 174.09187)
    assert (r.status, r.name) == ("ok", "Paihia")
    url, headers = fetch.calls[0]
    assert "lat=-35.28" in url and "lon=174.09" in url and "35.28123" not in url
    assert "PhotoOrganiser" in headers["User-Agent"] and "me@example.com" in headers["User-Agent"]


def test_cache_prevents_repeat_requests_and_persists(tmp_path):
    cache = tmp_path / "cache.json"
    fetch = FakeFetch([{"address": {"city": "Whangārei"}}])
    g = make(fetch, cache_path=cache)
    g.lookup(-35.7251, 174.3237)
    again = g.lookup(-35.7262, 174.3249)  # same ~1 km cell
    assert again.from_cache and again.name == "Whangārei" and len(fetch.calls) == 1
    g2 = make(FakeFetch([]), cache_path=cache)  # a new run reads the saved cache
    assert g2.lookup(-35.7251, 174.3237).name == "Whangārei"


def test_rate_limit_waits_between_requests():
    fetch = FakeFetch([{"address": {"city": "A"}}, {"address": {"city": "B"}}])
    g = make(fetch)
    g.lookup(10.0, 10.0)
    g.lookup(20.0, 20.0)
    assert g.sleeps and g.sleeps[0] >= 1.0


def test_failures_are_reported_not_cached_and_trip_circuit_breaker():
    fetch = FakeFetch([OSError("offline")] * 3)
    g = make(fetch)
    for lat in (1.0, 2.0, 3.0):
        r = g.lookup(lat, 10.0)
        assert r.status == "failed"
    assert g.disabled_reason
    assert g.lookup(4.0, 10.0).status == "failed" and len(fetch.calls) == 3  # no more requests


def test_http_429_stops_immediately():
    err = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
    g = make(FakeFetch([err]))
    assert g.lookup(1.0, 1.0).status == "failed"
    assert "429" in g.disabled_reason


def test_no_result_is_distinct_from_failure_and_cached():
    fetch = FakeFetch([{"error": "Unable to geocode"}])
    g = make(fetch)
    assert g.lookup(0.5, -30.0).status == "no_result"
    assert g.lookup(0.5, -30.0).status == "no_result" and len(fetch.calls) == 1
