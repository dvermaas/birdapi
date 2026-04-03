# bird 🐦

A Python CLI and library for X/Twitter's GraphQL API using cookie-based authentication — no API key required.

> **Disclaimer:** This uses X/Twitter's undocumented internal GraphQL API. X can change endpoints or rotate query IDs at any time. Expect occasional breakage.

## Installation

```bash
pip install -e .
```

Requires Python 3.10+.

## Quick start

### 1. Configure credentials

```bash
python -m bird.cli configure
```

You'll be prompted for two cookies from your active X/Twitter session:

1. Open [x.com](https://x.com) and log in
2. Open DevTools → Application → Cookies → `https://x.com`
3. Copy the values of **`auth_token`** and **`ct0`**

Credentials are saved to `~/.config/bird/credentials.json` and used automatically by all commands.

### 2. Run commands

```bash
python -m bird.cli whoami
python -m bird.cli search "from:username" -n 10
python -m bird.cli tweet "Hello from Python"
```

## Commands

| Command | Description |
|---|---|
| `configure` | Save credentials interactively |
| `whoami` | Show the authenticated account |
| `check` | Show where credentials are loaded from |
| `tweet "<text>"` | Post a new tweet |
| `reply <id-or-url> "<text>"` | Reply to a tweet |
| `read <id-or-url>` | Fetch a tweet |
| `thread <id-or-url>` | Show the full conversation thread |
| `replies <id-or-url>` | List replies to a tweet |
| `search "<query>"` | Search for tweets |
| `mentions` | Find tweets mentioning you |
| `user-tweets @handle` | Get tweets from a user's timeline |
| `home` | Your "For You" timeline |
| `home --following` | Your "Following" timeline |
| `bookmarks` | List your bookmarks |
| `unbookmark <id-or-url>` | Remove a bookmark |
| `likes` | List your liked tweets |
| `following` | Users you follow |
| `followers` | Users that follow you |
| `lists` | Your owned lists |
| `list-timeline <id-or-url>` | Tweets from a list |
| `news` | News and trending topics from Explore |
| `about @handle` | "About this account" info |
| `query-ids` | Inspect or refresh GraphQL query ID cache |

### Common options

```bash
--json              Output raw JSON
-n / --count N      Number of results (default varies per command)
--cursor STRING     Resume pagination from a cursor
--max-pages N       Limit number of pages fetched
--timeout MS        Request timeout in milliseconds
```

### Auth options (override saved credentials)

```bash
--auth-token TOKEN
--ct0 TOKEN

# Or via environment variables
AUTH_TOKEN=... CT0=... python -m bird.cli whoami
```

### Examples

```bash
# Search and output JSON
python -m bird.cli search "python asyncio" -n 20 --json

# Get someone's recent tweets
python -m bird.cli user-tweets @gvanrossum -n 50

# Fetch a full thread
python -m bird.cli thread https://x.com/user/status/1234567890123456789

# List bookmarks from a specific folder
python -m bird.cli bookmarks --folder-id 1234567890123456789 -n 50

# Fetch AI-curated news
python -m bird.cli news --ai-only -n 10

# Paginate following list
python -m bird.cli following -n 200 --json
```

## Library usage

```python
from bird import TwitterClient

client = TwitterClient(auth_token="...", ct0="...")

# Search
tweets, next_cursor = client.search("from:gvanrossum", count=20)
for tweet in tweets:
    print(f"@{tweet.author.username}: {tweet.text}")

# Post
tweet_id = client.tweet("Hello from Python!")

# Reply
client.reply("Thanks!", reply_to_tweet_id=tweet_id)

# Bookmarks
tweets, cursor = client.get_bookmarks(count=50)

# Engagement
client.like(tweet_id)
client.retweet(tweet_id)
client.bookmark(tweet_id)

# News
items = client.get_news(count=10, ai_only=True)
```

### Context manager

```python
with TwitterClient(auth_token="...", ct0="...") as client:
    user = client.get_current_user()
    print(user.username)
```

## Project structure

```
src/bird/
  client.py        TwitterClient — all API methods
  cli.py           Click CLI entry point
  _config.py       Credential storage (~/.config/bird/credentials.json)
  _constants.py    API URLs, bearer token, fallback query IDs
  _features.py     GraphQL feature-flag payloads
  _models.py       Dataclasses: Tweet, User, TwitterList, NewsItem, …
  _utils.py        Response parsing utilities
  _query_ids.py    Runtime query ID cache (scraped from x.com bundles)
tests/
  test_utils.py    Unit tests (no network required)
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/
ruff check src/ tests/
```

## Credential resolution order

1. `--auth-token` / `--ct0` CLI flags
2. `AUTH_TOKEN` / `CT0` environment variables
3. `~/.config/bird/credentials.json` (written by `bird configure`)
