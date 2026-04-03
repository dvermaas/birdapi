"""Unit tests for _utils.py — no network required."""

import pytest
from bird._utils import (
    extract_bookmark_folder_id,
    extract_cursor_from_instructions,
    extract_list_id,
    extract_tweet_id,
    map_tweet_result,
    normalize_handle,
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
