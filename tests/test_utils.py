"""Unit tests for _utils.py — no network required."""

from datetime import timezone

import pytest
from bird._utils import (
    extract_bookmark_folder_id,
    extract_cursor_from_instructions,
    extract_list_id,
    extract_tweet_id,
    map_tweet_result,
    map_user_profile_result,
    normalize_handle,
    parse_tweet_datetime,
    parse_tweets_from_instructions,
    render_content_state,
)


# ---------------------------------------------------------------------------
# normalize_handle
# ---------------------------------------------------------------------------

def test_normalize_handle_strips_at():
    assert normalize_handle("@steipete") == "steipete"

def test_normalize_handle_no_at():
    assert normalize_handle("steipete") == "steipete"

def test_normalize_handle_invalid_chars():
    assert normalize_handle("has space") is None

def test_normalize_handle_too_long():
    assert normalize_handle("a" * 16) is None

def test_normalize_handle_empty():
    assert normalize_handle("") is None


# ---------------------------------------------------------------------------
# ID extraction
# ---------------------------------------------------------------------------

def test_extract_tweet_id_bare():
    assert extract_tweet_id("1234567890123456789") == "1234567890123456789"

def test_extract_tweet_id_url():
    assert extract_tweet_id("https://x.com/user/status/1234567890123456789") == "1234567890123456789"

def test_extract_tweet_id_twitter_url():
    assert extract_tweet_id("https://twitter.com/steipete/status/1234567890123456789") == "1234567890123456789"

def test_extract_tweet_id_none():
    assert extract_tweet_id("not-a-tweet") is None

def test_extract_list_id_bare():
    assert extract_list_id("1234567890") == "1234567890"

def test_extract_list_id_url():
    assert extract_list_id("https://x.com/i/lists/1234567890") == "1234567890"

def test_extract_bookmark_folder_id_url():
    assert extract_bookmark_folder_id("https://x.com/i/bookmarks/1234567890") == "1234567890"


# ---------------------------------------------------------------------------
# render_content_state (Draft.js)
# ---------------------------------------------------------------------------

def test_render_content_state_empty():
    assert render_content_state(None) is None
    assert render_content_state({}) is None

def test_render_content_state_simple_paragraph():
    cs = {
        "blocks": [{"type": "unstyled", "text": "Hello world", "entityRanges": []}],
        "entityMap": [],
    }
    assert render_content_state(cs) == "Hello world"

def test_render_content_state_headers():
    cs = {
        "blocks": [
            {"type": "header-one", "text": "Title", "entityRanges": []},
            {"type": "header-two", "text": "Sub", "entityRanges": []},
        ],
        "entityMap": [],
    }
    result = render_content_state(cs)
    assert result == "# Title\n\n## Sub"

def test_render_content_state_list():
    cs = {
        "blocks": [
            {"type": "unordered-list-item", "text": "Item A", "entityRanges": []},
            {"type": "unordered-list-item", "text": "Item B", "entityRanges": []},
        ],
        "entityMap": [],
    }
    result = render_content_state(cs)
    assert result == "- Item A\n\n- Item B"

def test_render_content_state_divider():
    cs = {
        "blocks": [{"type": "atomic", "text": " ", "entityRanges": [{"key": 0, "offset": 0, "length": 1}]}],
        "entityMap": [{"key": "0", "value": {"type": "DIVIDER", "data": {}}}],
    }
    result = render_content_state(cs)
    assert result == "---"


# ---------------------------------------------------------------------------
# map_tweet_result
# ---------------------------------------------------------------------------

def _make_raw_tweet(tweet_id="1", text="Hello", username="alice", name="Alice", user_id="u1"):
    return {
        "rest_id": tweet_id,
        "core": {
            "user_results": {
                "result": {
                    "rest_id": user_id,
                    "legacy": {"screen_name": username, "name": name},
                }
            }
        },
        "legacy": {
            "full_text": text,
            "created_at": "Thu Apr 03 12:00:00 +0000 2025",
            "reply_count": 0,
            "retweet_count": 1,
            "favorite_count": 10,
            "conversation_id_str": tweet_id,
        },
    }


def test_map_tweet_result_basic():
    raw = _make_raw_tweet()
    tweet = map_tweet_result(raw)
    assert tweet is not None
    assert tweet.id == "1"
    assert tweet.text == "Hello"
    assert tweet.author.username == "alice"
    assert tweet.like_count == 10

def test_map_tweet_result_missing_rest_id():
    raw = _make_raw_tweet()
    raw.pop("rest_id")
    assert map_tweet_result(raw) is None

def test_map_tweet_result_missing_username():
    raw = _make_raw_tweet()
    raw["core"]["user_results"]["result"]["legacy"].pop("screen_name")
    assert map_tweet_result(raw) is None

def test_map_tweet_result_view_count():
    raw = _make_raw_tweet()
    raw["views"] = {"count": "12345", "state": "EnabledWithCount"}
    tweet = map_tweet_result(raw)
    assert tweet.view_count == 12345

def test_map_tweet_result_view_count_legacy_ext_views():
    raw = _make_raw_tweet()
    raw["legacy"]["ext_views"] = {"count": "777"}
    tweet = map_tweet_result(raw)
    assert tweet.view_count == 777

def test_map_tweet_result_view_count_missing_or_invalid():
    raw = _make_raw_tweet()
    assert map_tweet_result(raw).view_count is None
    raw["views"] = {"state": "Enabled"}  # no count
    assert map_tweet_result(raw).view_count is None
    raw["views"] = {"count": "not-a-number"}
    assert map_tweet_result(raw).view_count is None

def test_map_tweet_result_author_avatar_new_schema():
    raw = _make_raw_tweet()
    raw["core"]["user_results"]["result"]["avatar"] = {
        "image_url": "https://pbs.twimg.com/profile_images/1/x_normal.jpg"
    }
    tweet = map_tweet_result(raw)
    assert tweet.author.profile_image_url == "https://pbs.twimg.com/profile_images/1/x_normal.jpg"

def test_map_tweet_result_author_avatar_legacy():
    raw = _make_raw_tweet()
    raw["core"]["user_results"]["result"]["legacy"]["profile_image_url_https"] = (
        "https://pbs.twimg.com/profile_images/2/y_normal.jpg"
    )
    tweet = map_tweet_result(raw)
    assert tweet.author.profile_image_url == "https://pbs.twimg.com/profile_images/2/y_normal.jpg"

def test_map_tweet_result_quoted_tweet():
    inner = _make_raw_tweet("2", "Quoted", "bob", "Bob", "u2")
    outer = _make_raw_tweet("1", "Outer")
    outer["quoted_status_result"] = {"result": inner}
    tweet = map_tweet_result(outer, quote_depth=1)
    assert tweet is not None
    assert tweet.quoted_tweet is not None
    assert tweet.quoted_tweet.id == "2"

def test_map_tweet_result_no_quoted_at_depth_0():
    inner = _make_raw_tweet("2", "Quoted", "bob", "Bob", "u2")
    outer = _make_raw_tweet("1", "Outer")
    outer["quoted_status_result"] = {"result": inner}
    tweet = map_tweet_result(outer, quote_depth=0)
    assert tweet is not None
    assert tweet.quoted_tweet is None


# ---------------------------------------------------------------------------
# parse_tweets_from_instructions
# ---------------------------------------------------------------------------

def _instructions_with_tweet(raw_tweet):
    return [
        {
            "entries": [
                {
                    "content": {
                        "itemContent": {
                            "tweet_results": {"result": raw_tweet}
                        }
                    }
                }
            ]
        }
    ]


def test_parse_tweets_from_instructions():
    raw = _make_raw_tweet()
    instructions = _instructions_with_tweet(raw)
    tweets = parse_tweets_from_instructions(instructions)
    assert len(tweets) == 1
    assert tweets[0].id == "1"


def test_parse_tweets_deduplication():
    raw = _make_raw_tweet()
    instructions = _instructions_with_tweet(raw) + _instructions_with_tweet(raw)
    tweets = parse_tweets_from_instructions(instructions)
    assert len(tweets) == 1


def test_map_tweet_result_visibility_wrapper():
    # Visibility-gated tweets nest the real tweet under .tweet with no top rest_id.
    inner = _make_raw_tweet()
    wrapped = {"__typename": "TweetWithVisibilityResults", "tweet": inner}
    tweet = map_tweet_result(wrapped)
    assert tweet is not None
    assert tweet.id == "1"
    assert tweet.text == "Hello"


def test_parse_tweet_datetime_valid():
    dt = parse_tweet_datetime("Sun Jun 07 23:11:05 +0000 2026")
    assert dt is not None
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 6, 7, 23)
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0


def test_parse_tweet_datetime_invalid():
    assert parse_tweet_datetime(None) is None
    assert parse_tweet_datetime("") is None
    assert parse_tweet_datetime("not a date") is None


def test_parse_tweet_datetime_is_comparable():
    older = parse_tweet_datetime("Mon Jan 01 00:00:00 +0000 2024")
    newer = parse_tweet_datetime("Sun Jun 07 23:11:05 +0000 2026")
    cutoff = parse_tweet_datetime("Mon Jan 01 00:00:00 +0000 2026")
    assert older < cutoff < newer
    assert cutoff.tzinfo == timezone.utc or cutoff.utcoffset().total_seconds() == 0


def test_parse_tweets_from_instructions_visibility_wrapper():
    # Regression: accounts under visibility gating return all tweets wrapped as
    # TweetWithVisibilityResults; without unwrapping the parser yielded 0 tweets.
    inner = _make_raw_tweet()
    wrapped = {"__typename": "TweetWithVisibilityResults", "tweet": inner}
    tweets = parse_tweets_from_instructions(_instructions_with_tweet(wrapped))
    assert len(tweets) == 1
    assert tweets[0].id == "1"


# ---------------------------------------------------------------------------
# extract_cursor_from_instructions
# ---------------------------------------------------------------------------

def test_extract_cursor():
    instructions = [
        {
            "entries": [
                {"content": {"cursorType": "Bottom", "value": "next-cursor-abc"}}
            ]
        }
    ]
    assert extract_cursor_from_instructions(instructions) == "next-cursor-abc"

def test_extract_cursor_missing():
    assert extract_cursor_from_instructions([]) is None
    assert extract_cursor_from_instructions(None) is None


# ---------------------------------------------------------------------------
# map_user_profile_result
# ---------------------------------------------------------------------------

def _make_raw_user_profile():
    """Trimmed UserByScreenName user.result payload (new schema)."""
    return {
        "__typename": "User",
        "rest_id": "1120633726478823425",
        "avatar": {"image_url": "https://pbs.twimg.com/profile_images/1/a_normal.jpg"},
        "core": {
            "created_at": "Tue Apr 23 10:21:15 +0000 2019",
            "name": "Volodymyr Zelenskyy",
            "screen_name": "ZelenskyyUa",
        },
        "dm_permissions": {"can_dm": False},
        "is_blue_verified": True,
        "legacy": {
            "description": "President of Ukraine",
            "entities": {
                "url": {
                    "urls": [
                        {
                            "display_url": "president.gov.ua",
                            "expanded_url": "https://www.president.gov.ua",
                            "url": "https://t.co/ctVL0atMBQ",
                        }
                    ]
                }
            },
            "favourites_count": 214,
            "followers_count": 8515293,
            "friends_count": 1,
            "listed_count": 18781,
            "media_count": 7501,
            "profile_banner_url": "https://pbs.twimg.com/profile_banners/1/1692773060",
            "statuses_count": 15669,
            "url": "https://t.co/ctVL0atMBQ",
        },
        "location": {"location": "Україна"},
        "privacy": {"protected": False},
        "profile_bio": {"description": "President of Ukraine"},
        "verification": {"verified": False, "verified_type": "Government"},
        "verification_info": {
            "is_identity_verified": False,
            "reason": {"verified_since_msec": "1559215763761"},
        },
    }


def test_map_user_profile_result_full():
    p = map_user_profile_result(_make_raw_user_profile())
    assert p is not None
    assert p.id == "1120633726478823425"
    assert p.username == "ZelenskyyUa"
    assert p.name == "Volodymyr Zelenskyy"
    assert p.description == "President of Ukraine"
    assert p.location == "Україна"
    assert p.website == "https://www.president.gov.ua"
    assert p.created_at == "Tue Apr 23 10:21:15 +0000 2019"
    assert p.followers_count == 8515293
    assert p.following_count == 1
    assert p.tweet_count == 15669
    assert p.media_count == 7501
    assert p.listed_count == 18781
    assert p.likes_count == 214
    assert p.is_blue_verified is True
    assert p.is_verified is False
    assert p.verified_type == "Government"
    assert p.is_identity_verified is False
    assert p.verified_since == "2019-05-30T11:29:23.761000+00:00"
    assert p.profile_image_url == "https://pbs.twimg.com/profile_images/1/a_normal.jpg"
    assert p.profile_banner_url == "https://pbs.twimg.com/profile_banners/1/1692773060"
    assert p.is_protected is False
    assert p.can_dm is False
    assert p._raw is None


def test_map_user_profile_result_include_raw():
    raw = _make_raw_user_profile()
    p = map_user_profile_result(raw, include_raw=True)
    assert p._raw is raw


def test_map_user_profile_result_legacy_schema():
    # Older payloads carry name/screen_name under legacy instead of core.
    p = map_user_profile_result({
        "rest_id": "42",
        "legacy": {
            "screen_name": "alice",
            "name": "Alice",
            "description": "hi",
            "followers_count": 5,
            "profile_image_url_https": "https://pbs.twimg.com/profile_images/2/b_normal.jpg",
            "protected": True,
        },
    })
    assert p is not None
    assert p.username == "alice"
    assert p.name == "Alice"
    assert p.followers_count == 5
    assert p.profile_image_url == "https://pbs.twimg.com/profile_images/2/b_normal.jpg"
    assert p.is_protected is True


def test_map_user_profile_result_invalid():
    assert map_user_profile_result(None) is None
    assert map_user_profile_result({}) is None
    assert map_user_profile_result({"rest_id": "1"}) is None  # no screen_name
