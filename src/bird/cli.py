"""bird CLI — X/Twitter GraphQL client."""

from __future__ import annotations

import io
import json
import os
import sys
from typing import Optional

import click

# Ensure stdout can handle Unicode on Windows (e.g. cp1252 terminals)
if sys.stdout and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from .client import TwitterClient
from ._config import load_credentials, resolve_credentials, save_credentials
from ._utils import extract_tweet_id, extract_list_id, normalize_handle


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _make_client(
    auth_token: Optional[str],
    ct0: Optional[str],
    timeout: Optional[float],
) -> TwitterClient:
    tok, csrf = resolve_credentials(auth_token, ct0)
    if not tok or not csrf:
        click.echo(
            "Error: credentials not found.\n"
            "Run  bird configure  to save your auth_token and ct0.",
            err=True,
        )
        sys.exit(1)
    return TwitterClient(tok, csrf, timeout=timeout)


# ---------------------------------------------------------------------------
# Global options
# ---------------------------------------------------------------------------

@click.group()
@click.pass_context
@click.option("--auth-token", envvar=["AUTH_TOKEN", "TWITTER_AUTH_TOKEN"], hidden=True)
@click.option("--ct0", envvar=["CT0", "TWITTER_CT0"], hidden=True)
@click.option("--timeout", type=float, default=None, envvar="BIRD_TIMEOUT_MS",
              help="Request timeout in milliseconds.")
@click.option("--json", "as_json", is_flag=True)
@click.option("--quote-depth", type=int, default=1, envvar="BIRD_QUOTE_DEPTH")
def main(ctx: click.Context, auth_token, ct0, timeout, as_json, quote_depth):
    """bird — fast X/Twitter CLI (cookie auth, no browser extraction)."""
    ctx.ensure_object(dict)
    ctx.obj["auth_token"] = auth_token
    ctx.obj["ct0"] = ct0
    ctx.obj["timeout"] = timeout / 1000 if timeout else None
    ctx.obj["as_json"] = as_json
    ctx.obj["quote_depth"] = quote_depth


def _client(ctx) -> TwitterClient:
    o = ctx.obj
    return _make_client(o["auth_token"], o["ct0"], o["timeout"])


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

import html as _html

_SEPARATOR = "\u2500" * 50  # ──────────────────────────────────────────────────


def _unescape(text: str) -> str:
    return _html.unescape(text)


def _format_tweet(tweet) -> str:
    lines: list[str] = []

    # Header: @username (Full Name):
    lines.append(f"@{tweet.author.username} ({tweet.author.name}):")

    # Tweet text
    lines.append(_unescape(tweet.text))

    # Quoted tweet box
    if tweet.quoted_tweet:
        qt = tweet.quoted_tweet
        lines.append(f"\u250c\u2500 QT @{qt.author.username}:")
        for body_line in _unescape(qt.text).splitlines():
            lines.append(f"\u2502 {body_line}")
        if qt.media:
            for m in qt.media:
                icon = "\U0001f3ac" if m.type in ("video", "animated_gif") else "\U0001f5bc\ufe0f"
                lines.append(f"\u2502 {icon} {m.url}")
        lines.append(f"\u2514\u2500 https://x.com/{qt.author.username}/status/{qt.id}")

    # Media on the outer tweet
    if tweet.media:
        for m in tweet.media:
            icon = "\U0001f3ac" if m.type in ("video", "animated_gif") else "\U0001f5bc\ufe0f"
            lines.append(f"{icon} {m.url}")

    # Metadata
    if tweet.created_at:
        lines.append(f"\U0001f4c5 {tweet.created_at}")
    lines.append(f"\U0001f517 https://x.com/{tweet.author.username}/status/{tweet.id}")
    lines.append(_SEPARATOR)

    return "\n".join(lines)


def _dump_tweet(tweet, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(_tweet_to_dict(tweet), ensure_ascii=False))
    else:
        click.echo(_format_tweet(tweet))


def _dump_tweets(tweets, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps([_tweet_to_dict(t) for t in tweets], ensure_ascii=False))
    else:
        for t in tweets:
            click.echo(_format_tweet(t))


def _tweet_to_dict(tweet) -> dict:
    d: dict = {
        "id": tweet.id,
        "text": tweet.text,
        "author": {"username": tweet.author.username, "name": tweet.author.name},
        "authorId": tweet.author_id,
        "createdAt": tweet.created_at,
        "replyCount": tweet.reply_count,
        "retweetCount": tweet.retweet_count,
        "likeCount": tweet.like_count,
        "conversationId": tweet.conversation_id,
        "inReplyToStatusId": tweet.in_reply_to_status_id,
    }
    if tweet.quoted_tweet:
        d["quotedTweet"] = _tweet_to_dict(tweet.quoted_tweet)
    if tweet.media:
        d["media"] = [
            {k: v for k, v in m.__dict__.items() if v is not None}
            for m in tweet.media
        ]
    return d


def _user_to_dict(user) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "description": user.description,
        "followersCount": user.followers_count,
        "followingCount": user.following_count,
        "isBlueVerified": user.is_blue_verified,
        "profileImageUrl": user.profile_image_url,
        "createdAt": user.created_at,
    }


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

@main.command()
@click.argument("tweet_id_or_url")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def read(ctx, tweet_id_or_url, as_json):
    """Fetch and display a tweet by ID or URL."""
    tweet_id = extract_tweet_id(tweet_id_or_url)
    if not tweet_id:
        click.echo(f"Error: cannot parse tweet ID from {tweet_id_or_url!r}", err=True)
        sys.exit(1)
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        tweet = client.get_tweet(tweet_id)
    if not tweet:
        click.echo("Tweet not found.", err=True)
        sys.exit(1)
    _dump_tweet(tweet, as_json)


# ---------------------------------------------------------------------------
# thread / replies
# ---------------------------------------------------------------------------

@main.command()
@click.argument("tweet_id_or_url")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def thread(ctx, tweet_id_or_url, as_json):
    """Show the full conversation thread for a tweet."""
    tweet_id = extract_tweet_id(tweet_id_or_url)
    if not tweet_id:
        click.echo(f"Error: cannot parse tweet ID from {tweet_id_or_url!r}", err=True)
        sys.exit(1)
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        tweets = client.get_thread(tweet_id)
    _dump_tweets(tweets, as_json)


@main.command()
@click.argument("tweet_id_or_url")
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def replies(ctx, tweet_id_or_url, count, as_json):
    """List replies to a tweet."""
    tweet_id = extract_tweet_id(tweet_id_or_url)
    if not tweet_id:
        click.echo(f"Error: cannot parse tweet ID from {tweet_id_or_url!r}", err=True)
        sys.exit(1)
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        tweets = client.get_replies(tweet_id)
    _dump_tweets(tweets[:count], as_json)


# ---------------------------------------------------------------------------
# tweet / reply
# ---------------------------------------------------------------------------

@main.command(name="tweet")
@click.argument("text")
@click.pass_context
def post_tweet(ctx, text):
    """Post a new tweet."""
    with _client(ctx) as client:
        tweet_id = client.tweet(text)
    if tweet_id:
        click.echo(f"Posted: https://x.com/i/status/{tweet_id}")
    else:
        click.echo("Failed to post tweet.", err=True)
        sys.exit(1)


@main.command(name="reply")
@click.argument("tweet_id_or_url")
@click.argument("text")
@click.pass_context
def post_reply(ctx, tweet_id_or_url, text):
    """Reply to a tweet."""
    tweet_id = extract_tweet_id(tweet_id_or_url)
    if not tweet_id:
        click.echo(f"Error: cannot parse tweet ID from {tweet_id_or_url!r}", err=True)
        sys.exit(1)
    with _client(ctx) as client:
        new_id = client.reply(text, tweet_id)
    if new_id:
        click.echo(f"Replied: https://x.com/i/status/{new_id}")
    else:
        click.echo("Failed to post reply.", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# search / mentions
# ---------------------------------------------------------------------------

@main.command()
@click.argument("query")
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--cursor", default=None)
@click.option("--max-pages", type=int, default=None)
@click.pass_context
def search(ctx, query, count, as_json, cursor, max_pages):
    """Search for tweets matching a query."""
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        tweets, next_cursor = client.search(query, count, cursor=cursor, max_pages=max_pages)
    if as_json:
        click.echo(json.dumps({"tweets": [_tweet_to_dict(t) for t in tweets], "nextCursor": next_cursor}, ensure_ascii=False))
    else:
        _dump_tweets(tweets, False)


@main.command()
@click.option("--user", default=None, help="@handle to search mentions for")
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def mentions(ctx, user, count, as_json):
    """Find tweets mentioning a user (defaults to authenticated user)."""
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        tweets, _ = client.get_mentions(user, count)
    _dump_tweets(tweets, as_json)


# ---------------------------------------------------------------------------
# user-tweets
# ---------------------------------------------------------------------------

@main.command("user-tweets")
@click.argument("handle")
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--cursor", default=None)
@click.pass_context
def user_tweets(ctx, handle, count, as_json, cursor):
    """Get tweets from a user's profile timeline."""
    as_json = as_json or ctx.obj.get("as_json")
    norm = normalize_handle(handle)
    if not norm:
        click.echo(f"Invalid handle: {handle!r}", err=True)
        sys.exit(1)
    with _client(ctx) as client:
        user = client.get_user_id_by_username(norm)
        if not user:
            click.echo(f"User @{norm} not found.", err=True)
            sys.exit(1)
        tweets, next_cursor = client.get_user_tweets(user.id, count, cursor=cursor)
    if as_json:
        click.echo(json.dumps({"tweets": [_tweet_to_dict(t) for t in tweets], "nextCursor": next_cursor}, ensure_ascii=False))
    else:
        _dump_tweets(tweets, False)


# ---------------------------------------------------------------------------
# bookmarks / unbookmark
# ---------------------------------------------------------------------------

@main.command()
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--folder-id", default=None)
@click.option("--all", "fetch_all", is_flag=True)
@click.option("--max-pages", type=int, default=None)
@click.option("--cursor", default=None)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def bookmarks(ctx, count, folder_id, fetch_all, max_pages, cursor, as_json):
    """List bookmarked tweets."""
    as_json = as_json or ctx.obj.get("as_json")
    limit = -1 if fetch_all else count
    with _client(ctx) as client:
        tweets, next_cursor = client.get_bookmarks(
            limit, folder_id=folder_id, cursor=cursor, max_pages=max_pages
        )
    if as_json:
        click.echo(json.dumps({"tweets": [_tweet_to_dict(t) for t in tweets], "nextCursor": next_cursor}, ensure_ascii=False))
    else:
        _dump_tweets(tweets, False)


@main.command()
@click.argument("tweet_ids_or_urls", nargs=-1, required=True)
@click.pass_context
def unbookmark(ctx, tweet_ids_or_urls):
    """Remove one or more bookmarks by tweet ID or URL."""
    with _client(ctx) as client:
        for val in tweet_ids_or_urls:
            tweet_id = extract_tweet_id(val)
            if not tweet_id:
                click.echo(f"Cannot parse ID from {val!r}", err=True)
                continue
            ok = client.unbookmark(tweet_id)
            status = "Removed" if ok else "Failed to remove"
            click.echo(f"{status}: {tweet_id}")


# ---------------------------------------------------------------------------
# likes
# ---------------------------------------------------------------------------

@main.command()
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--cursor", default=None)
@click.pass_context
def likes(ctx, count, as_json, cursor):
    """List liked tweets."""
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        tweets, next_cursor = client.get_likes(count, cursor=cursor)
    if as_json:
        click.echo(json.dumps({"tweets": [_tweet_to_dict(t) for t in tweets], "nextCursor": next_cursor}, ensure_ascii=False))
    else:
        _dump_tweets(tweets, False)


# ---------------------------------------------------------------------------
# home
# ---------------------------------------------------------------------------

@main.command()
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--following", is_flag=True, help="Show Following (chronological) feed")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def home(ctx, count, following, as_json):
    """Fetch home timeline (For You or Following feed)."""
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        if following:
            tweets = client.get_home_latest_timeline(count)
        else:
            tweets = client.get_home_timeline(count)
    _dump_tweets(tweets, as_json)


# ---------------------------------------------------------------------------
# following / followers
# ---------------------------------------------------------------------------

@main.command()
@click.option("--user", default=None, help="User ID to look up (defaults to self)")
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--cursor", default=None)
@click.pass_context
def following(ctx, user, count, as_json, cursor):
    """List users the authenticated user (or --user) follows."""
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        if user:
            uid = user
        else:
            me = client.get_current_user()
            if not me:
                click.echo("Could not determine current user.", err=True)
                sys.exit(1)
            uid = me.id
        users, next_cursor = client.get_following(uid, count, cursor=cursor)
    if as_json:
        click.echo(json.dumps({"users": [_user_to_dict(u) for u in users], "nextCursor": next_cursor}, ensure_ascii=False))
    else:
        for u in users:
            click.echo(f"@{u.username} — {u.name}")


@main.command()
@click.option("--user", default=None, help="User ID to look up (defaults to self)")
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--cursor", default=None)
@click.pass_context
def followers(ctx, user, count, as_json, cursor):
    """List users that follow the authenticated user (or --user)."""
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        if user:
            uid = user
        else:
            me = client.get_current_user()
            if not me:
                click.echo("Could not determine current user.", err=True)
                sys.exit(1)
            uid = me.id
        users, next_cursor = client.get_followers(uid, count, cursor=cursor)
    if as_json:
        click.echo(json.dumps({"users": [_user_to_dict(u) for u in users], "nextCursor": next_cursor}, ensure_ascii=False))
    else:
        for u in users:
            click.echo(f"@{u.username} — {u.name}")


# ---------------------------------------------------------------------------
# lists / list-timeline
# ---------------------------------------------------------------------------

@main.command("lists")
@click.option("--member-of", is_flag=True, help="Show lists you're a member of")
@click.option("-n", "--count", default=100, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def list_lists(ctx, member_of, count, as_json):
    """List your owned lists or memberships."""
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        if member_of:
            lst = client.get_list_memberships(count)
        else:
            lst = client.get_owned_lists(count)
    if as_json:
        click.echo(json.dumps([
            {"id": l.id, "name": l.name, "memberCount": l.member_count, "isPrivate": l.is_private}
            for l in lst
        ], ensure_ascii=False))
    else:
        for l in lst:
            priv = " (private)" if l.is_private else ""
            click.echo(f"[{l.id}] {l.name}{priv} — {l.member_count or '?'} members")


@main.command("list-timeline")
@click.argument("list_id_or_url")
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--cursor", default=None)
@click.option("--max-pages", type=int, default=None)
@click.pass_context
def list_timeline(ctx, list_id_or_url, count, as_json, cursor, max_pages):
    """Get tweets from a list timeline."""
    list_id = extract_list_id(list_id_or_url)
    if not list_id:
        click.echo(f"Cannot parse list ID from {list_id_or_url!r}", err=True)
        sys.exit(1)
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        tweets, next_cursor = client.get_list_timeline(list_id, count, cursor=cursor, max_pages=max_pages)
    if as_json:
        click.echo(json.dumps({"tweets": [_tweet_to_dict(t) for t in tweets], "nextCursor": next_cursor}, ensure_ascii=False))
    else:
        _dump_tweets(tweets, False)


# ---------------------------------------------------------------------------
# news / trending
# ---------------------------------------------------------------------------

@main.command()
@click.option("-n", "--count", default=10, show_default=True)
@click.option("--ai-only", is_flag=True)
@click.option("--with-tweets", is_flag=True)
@click.option("--tweets-per-item", type=int, default=5, show_default=True)
@click.option("--for-you", "tab_for_you", is_flag=True)
@click.option("--news-only", "tab_news", is_flag=True)
@click.option("--sports", "tab_sports", is_flag=True)
@click.option("--entertainment", "tab_entertainment", is_flag=True)
@click.option("--trending-only", "tab_trending", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def news(ctx, count, ai_only, with_tweets, tweets_per_item,
         tab_for_you, tab_news, tab_sports, tab_entertainment, tab_trending, as_json):
    """Fetch news and trending topics from X's Explore tabs."""
    as_json = as_json or ctx.obj.get("as_json")
    tabs: list[str] = []
    if tab_for_you:
        tabs.append("forYou")
    if tab_news:
        tabs.append("news")
    if tab_sports:
        tabs.append("sports")
    if tab_entertainment:
        tabs.append("entertainment")
    if tab_trending:
        tabs.append("trending")
    if not tabs:
        tabs = ["forYou", "news", "sports", "entertainment"]
    with _client(ctx) as client:
        items = client.get_news(
            count,
            ai_only=ai_only,
            with_tweets=with_tweets,
            tweets_per_item=tweets_per_item,
            tabs=tabs,
        )
    if as_json:
        def _item_dict(item):
            d = {
                "id": item.id,
                "headline": item.headline,
                "category": item.category,
                "timeAgo": item.time_ago,
                "postCount": item.post_count,
                "description": item.description,
                "url": item.url,
            }
            if item.tweets:
                d["tweets"] = [_tweet_to_dict(t) for t in item.tweets]
            return d
        click.echo(json.dumps([_item_dict(i) for i in items], ensure_ascii=False))
    else:
        for item in items:
            parts = [item.headline]
            if item.category:
                parts.append(f"[{item.category}]")
            if item.time_ago:
                parts.append(item.time_ago)
            if item.post_count:
                parts.append(f"{item.post_count:,} posts")
            click.echo("  ".join(parts))


@main.command()
@click.option("-n", "--count", default=10, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def trending(ctx, count, as_json):
    """Alias for news --trending-only."""
    ctx.invoke(news, count=count, ai_only=False, with_tweets=False, tweets_per_item=5,
               tab_for_you=False, tab_news=False, tab_sports=False,
               tab_entertainment=False, tab_trending=True, as_json=as_json)


# ---------------------------------------------------------------------------
# about / whoami / check
# ---------------------------------------------------------------------------

@main.command()
@click.argument("handle")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def about(ctx, handle, as_json):
    """Get 'About this account' information for a user."""
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        profile = client.get_user_about_account(handle)
    if not profile:
        click.echo("No about information found.", err=True)
        sys.exit(1)
    if as_json:
        click.echo(json.dumps({
            "accountBasedIn": profile.account_based_in,
            "source": profile.source,
            "createdCountryAccurate": profile.created_country_accurate,
            "locationAccurate": profile.location_accurate,
            "learnMoreUrl": profile.learn_more_url,
        }, ensure_ascii=False))
    else:
        if profile.account_based_in:
            click.echo(f"Based in: {profile.account_based_in}")
        if profile.source:
            click.echo(f"Source: {profile.source}")
        if profile.created_country_accurate:
            click.echo(f"Created in: {profile.created_country_accurate}")


@main.command()
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def whoami(ctx, as_json):
    """Print which X account your cookies belong to."""
    as_json = as_json or ctx.obj.get("as_json")
    with _client(ctx) as client:
        user = client.get_current_user()
    if not user:
        click.echo("Could not determine current user.", err=True)
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(_user_to_dict(user), ensure_ascii=False))
    else:
        click.echo(f"@{user.username} ({user.name}) — id: {user.id}")


@main.command()
@click.pass_context
def check(ctx):
    """Show which credentials are available and where they came from."""
    import os as _os
    o = ctx.obj
    saved = load_credentials()

    sources: dict[str, str] = {}
    for key, flag_val, env_keys, saved_key in [
        ("auth_token", o.get("auth_token"), ["AUTH_TOKEN", "TWITTER_AUTH_TOKEN"], "auth_token"),
        ("ct0",        o.get("ct0"),        ["CT0", "TWITTER_CT0"],               "ct0"),
    ]:
        if flag_val:
            sources[key] = "flag"
        elif any(_os.environ.get(e) for e in env_keys):
            sources[key] = "env"
        elif saved.get(saved_key):
            sources[key] = "credentials file"
        else:
            sources[key] = "NOT SET"

    for key, source in sources.items():
        click.echo(f"{key:<12} {source}")


@main.command()
def configure():
    """Interactively save X/Twitter credentials (auth_token and ct0).

    Credentials are stored in ~/.config/bird/credentials.json and loaded
    automatically by all commands.

    \b
    Where to find these values:
      1. Log in to x.com in your browser
      2. Open DevTools -> Application -> Cookies -> https://x.com
      3. Copy the values of  auth_token  and  ct0
    """
    import sys

    saved = load_credentials()

    print("Configure bird credentials\n", flush=True)
    print("Where to find these: x.com DevTools -> Application -> Cookies -> https://x.com\n", flush=True)

    def _read(label: str, saved_key: str) -> str:
        current = saved.get(saved_key, "")
        hint = f" [{current[:8]}...] (Enter to keep)" if current else ""
        print(f"{label}{hint}: ", end="", flush=True)
        try:
            value = sys.stdin.readline().strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        return value or current

    auth_token = _read("auth_token", "auth_token")
    ct0 = _read("ct0", "ct0")

    if not auth_token or not ct0:
        print("Aborted — both values are required.", flush=True)
        sys.exit(1)

    print("\nValidating credentials...", flush=True)
    try:
        client = TwitterClient(auth_token, ct0, timeout=15)
        user = client.get_current_user()
        client.close()
    except Exception as exc:
        print(f"Error connecting to X: {exc}", flush=True)
        sys.exit(1)

    if not user:
        print("Could not verify credentials — check your auth_token and ct0.", flush=True)
        sys.exit(1)

    path = save_credentials(auth_token, ct0)
    print(f"Authenticated as @{user.username} ({user.name})", flush=True)
    print(f"Credentials saved to {path}", flush=True)


# ---------------------------------------------------------------------------
# query-ids
# ---------------------------------------------------------------------------

@main.command("query-ids")
@click.option("--fresh", is_flag=True, help="Force-refresh the cache from x.com bundles")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def query_ids_cmd(ctx, fresh, as_json):
    """Inspect or refresh the cached GraphQL query IDs."""
    from ._query_ids import query_id_store
    if fresh:
        from ._constants import FALLBACK_QUERY_IDS
        query_id_store.refresh(list(FALLBACK_QUERY_IDS.keys()), force=True)
        click.echo("Query IDs refreshed.")
    info = query_id_store.info()
    if as_json:
        click.echo(json.dumps(info, ensure_ascii=False))
    else:
        cached = info.get("cached", False)
        click.echo(f"Cache path: {info['cachePath']}")
        click.echo(f"Cached:     {cached}")
        if cached:
            click.echo(f"Age:        {info.get('ageSeconds', '?')}s  (TTL {info.get('ttl', '?')}s)")
            click.echo(f"Fresh:      {info.get('fresh', '?')}")
            ids = info.get("ids") or {}
            click.echo(f"Operations: {len(ids)} cached")


if __name__ == "__main__":
    main()
