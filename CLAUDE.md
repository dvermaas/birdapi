# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

A pure-Python CLI and library for X/Twitter's undocumented GraphQL API, using cookie-based authentication. No browser cookie extraction — credentials are supplied directly via environment variables or CLI flags.

**Stability caveat:** X can rotate GraphQL query IDs or change endpoints without notice. The `_query_ids.py` module handles auto-refresh from live x.com bundles.

## Commands

```bash
# Install (editable) with dev deps
pip install -e ".[dev]"

# Tests
pytest tests/                        # all unit tests (no network)
pytest tests/test_utils.py::test_X   # single test

# Lint / format
ruff check src/ tests/
ruff format src/ tests/

# CLI (requires AUTH_TOKEN + CT0 env vars or --auth-token / --ct0 flags)
bird --help
bird whoami
bird read <tweet-url-or-id>
bird search "from:steipete" -n 5
bird tweet "Hello from Python"
bird query-ids --fresh   # force-refresh GraphQL query ID cache
```

## Architecture

```
src/bird/
  __init__.py          Public re-exports (TwitterClient, Tweet, User, …)
  client.py            TwitterClient — all API methods
  cli.py               Click CLI entry point
  _constants.py        API URLs, bearer token, fallback query IDs
  _features.py         GraphQL feature-flag payloads (per-operation)
  _models.py           Dataclasses: Tweet, User, TwitterList, NewsItem, …
  _utils.py            Parsing: map_tweet_result, parse_tweets_from_instructions,
                       extract_cursor_from_instructions, render_content_state, …
  _query_ids.py        Runtime query ID store — scrapes x.com JS bundles,
                       caches to ~/.config/bird/query-ids-cache.json (24 h TTL)
tests/
  test_utils.py        Unit tests for _utils.py (no network)
```

### Authentication

Pass `auth_token` and `ct0` cookies (from an active X/Twitter web session):

```python
from bird import TwitterClient

client = TwitterClient(auth_token="...", ct0="...")
# or via env vars: AUTH_TOKEN, CT0
```

Or via CLI: `--auth-token` / `--ct0`, or `AUTH_TOKEN` / `CT0` env vars.

### Client API

```python
client.tweet(text)                          # → tweet_id str | None
client.reply(text, reply_to_id)             # → tweet_id str | None
client.get_tweet(tweet_id)                  # → Tweet | None
client.get_replies(tweet_id)                # → list[Tweet]
client.get_thread(tweet_id)                 # → list[Tweet]
client.search(query, count)                 # → (list[Tweet], next_cursor)
client.get_mentions(username, count)        # → (list[Tweet], next_cursor)
client.get_bookmarks(count)                 # → (list[Tweet], next_cursor)
client.get_likes(count)                     # → (list[Tweet], next_cursor)
client.get_user_tweets(user_id, count)      # → (list[Tweet], next_cursor)
client.get_home_timeline(count)             # → list[Tweet]
client.get_home_latest_timeline(count)      # → list[Tweet]
client.get_current_user()                   # → User | None
client.get_user_id_by_username(handle)      # → User | None
client.get_following(user_id, count)        # → (list[User], next_cursor)
client.get_followers(user_id, count)        # → (list[User], next_cursor)
client.like/unlike/retweet/unretweet/bookmark/unbookmark(tweet_id)  # → bool
client.follow/unfollow(user_id)             # → bool
client.get_owned_lists()                    # → list[TwitterList]
client.get_list_timeline(list_id, count)    # → (list[Tweet], next_cursor)
client.get_news(count, ai_only, tabs)       # → list[NewsItem]
client.get_user_about_account(username)     # → AboutProfile | None
client.refresh_query_ids()                  # → dict (cache info)
```

### Query ID caching

`_query_ids.QueryIdStore` (singleton: `query_id_store`) scrapes the live x.com
JS bundles to extract fresh query IDs. Results are cached at
`~/.config/bird/query-ids-cache.json` for 24 hours. On 404 responses the client
automatically retries after refreshing. The fallback IDs in `_constants.py` keep
the client working if the cache is missing.

### Tweet parsing flow

`parse_tweets_from_instructions(instructions)` → iterates GraphQL timeline
instructions → `_collect_tweet_results_from_entry` → `map_tweet_result` →
`Tweet` dataclass. Rich text (Notes/Articles in Draft.js format) is rendered to
Markdown by `render_content_state` in `_utils.py`.
