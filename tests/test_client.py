"""Unit tests for TwitterClient pagination/throttle internals — no network."""

from datetime import datetime, timedelta, timezone

from bird._models import Author, Tweet
from bird.client import TwitterClient


def _client():
    return TwitterClient(auth_token="x", ct0="y")


def _tweet(tid: str, dt: datetime, pinned_label: str = "") -> Tweet:
    return Tweet(
        id=tid,
        text=f"tweet {tid}{pinned_label}",
        author=Author(username="alice", name="Alice"),
        created_at=dt.strftime("%a %b %d %H:%M:%S %z %Y"),
    )


# ---------------------------------------------------------------------------
# _paginate: since-mode date filtering
# ---------------------------------------------------------------------------

def _make_fetcher(pages):
    """Return a fetch_page(cursor, count) that yields successive pages.

    `pages` is a list of (tweets, next_cursor).
    """
    state = {"i": 0}

    def fetch_page(cursor, count):
        i = state["i"]
        if i >= len(pages):
            return [], None, False, None
        tweets, cur = pages[i]
        state["i"] = i + 1
        return tweets, cur, False, None

    return fetch_page


def test_paginate_since_stops_and_filters():
    now = datetime(2026, 6, 7, tzinfo=timezone.utc)
    cutoff = now - timedelta(days=2)
    # Page 1: all newer than cutoff. Page 2: straddles cutoff (last is older).
    p1 = [_tweet("1", now), _tweet("2", now - timedelta(days=1))]
    p2 = [
        _tweet("3", now - timedelta(days=1, hours=12)),
        _tweet("4", now - timedelta(days=3)),
    ]
    p3 = [_tweet("5", now - timedelta(days=5))]  # should never be fetched
    fetch = _make_fetcher([(p1, "c1"), (p2, "c2"), (p3, "c3")])

    tweets, _, _ = _client()._paginate(fetch, limit=float("inf"), since=cutoff)
    ids = [t.id for t in tweets]
    assert ids == ["1", "2", "3"]  # "4" dropped (older), page 3 never fetched


def test_paginate_since_pinned_old_does_not_stop_early():
    now = datetime(2026, 6, 7, tzinfo=timezone.utc)
    cutoff = now - timedelta(days=2)
    # Pinned tweet (old) sits FIRST but the rest of the page is recent.
    pinned = _tweet("pin", now - timedelta(days=400), " [pinned]")
    p1 = [pinned, _tweet("1", now), _tweet("2", now - timedelta(days=1))]
    p2 = [_tweet("3", now - timedelta(days=3))]  # crosses cutoff -> stop after this
    fetch = _make_fetcher([(p1, "c1"), (p2, "c2")])

    tweets, _, _ = _client()._paginate(fetch, limit=float("inf"), since=cutoff)
    ids = [t.id for t in tweets]
    # Pinned dropped (older than cutoff), but page 2 WAS still fetched.
    assert "pin" not in ids
    assert ids == ["1", "2"]


def test_paginate_since_count_caps_result():
    now = datetime(2026, 6, 7, tzinfo=timezone.utc)
    cutoff = now - timedelta(days=30)
    p1 = [_tweet(str(i), now - timedelta(hours=i)) for i in range(20)]
    fetch = _make_fetcher([(p1, "c1"), (p1, "c2")])
    tweets, _, _ = _client()._paginate(fetch, limit=5, since=cutoff)
    assert len(tweets) == 5


# ---------------------------------------------------------------------------
# rate-limit throttle
# ---------------------------------------------------------------------------

def test_throttle_disabled_by_default(monkeypatch):
    slept = []
    monkeypatch.setattr("bird.client.time.sleep", lambda s: slept.append(s))
    c = _client()
    c._throttle()
    c._throttle()
    assert slept == []


def test_throttle_spaces_calls(monkeypatch):
    slept = []
    clock = {"t": 100.0}
    monkeypatch.setattr("bird.client.time.monotonic", lambda: clock["t"])
    monkeypatch.setattr("bird.client.time.sleep", lambda s: slept.append(s))
    c = TwitterClient(auth_token="x", ct0="y", min_request_interval=5.0)
    c._throttle()  # first call: last_request_at was 0, so elapsed huge -> no sleep
    assert slept == []
    # Second call immediately after: must wait the full interval.
    c._throttle()
    assert slept and abs(slept[0] - 5.0) < 1e-6
