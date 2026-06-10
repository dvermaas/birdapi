"""bird CLI — X/Twitter GraphQL client."""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone
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
    min_request_interval: float = 0.0,
) -> TwitterClient:
    tok, csrf = resolve_credentials(auth_token, ct0)
    if not tok or not csrf:
        click.echo(
            "Error: credentials not found.\n"
            "Run  bird configure  to save your auth_token and ct0.",
            err=True,
        )
        sys.exit(1)
    return TwitterClient(tok, csrf, timeout=timeout, min_request_interval=min_request_interval)


# ---------------------------------------------------------------------------
# Shorthand group: bird <tweet-id-or-url> → bird read <tweet-id-or-url>
# ---------------------------------------------------------------------------

class BirdGroup(click.Group):
    def resolve_command(self, ctx, args):
        cmd_name = args[0] if args else None
        if cmd_name and cmd_name not in self.commands and not cmd_name.startswith("-"):
            if cmd_name.isdigit() or "/status/" in cmd_name or "x.com" in cmd_name or "twitter.com" in cmd_name:
                args.insert(0, "read")
        return super().resolve_command(ctx, args)


# ---------------------------------------------------------------------------
# Global options
# ---------------------------------------------------------------------------

@click.group(cls=BirdGroup)
@click.pass_context
@click.option("--auth-token", envvar=["AUTH_TOKEN", "TWITTER_AUTH_TOKEN"], hidden=True)
@click.option("--ct0", envvar=["CT0", "TWITTER_CT0"], hidden=True)
@click.option("--timeout", type=float, default=None, envvar="BIRD_TIMEOUT_MS",
              help="Request timeout in milliseconds.")
@click.option("--rate-limit", "rate_limit", type=float, default=0.0, envvar="BIRD_RATE_LIMIT",
              help="Minimum seconds between calls to x.com (0 = off). e.g. --rate-limit 5")
@click.option("--json", "as_json", is_flag=True)
@click.option("--quote-depth", type=int, default=1, envvar="BIRD_QUOTE_DEPTH")
@click.option("--plain", is_flag=True, default=False,
              help="Plain output: no emoji, no color (stable for scripting).")
@click.option("--no-emoji", "no_emoji", is_flag=True, default=False,
              help="Disable emoji in output.")
@click.option("--no-color", "no_color", is_flag=True, default=False,
              help="Disable ANSI colors (or set NO_COLOR env var).")
def main(ctx: click.Context, auth_token, ct0, timeout, rate_limit, as_json, quote_depth, plain, no_emoji, no_color):
    """bird — fast X/Twitter CLI (cookie auth, no browser extraction)."""
    ctx.ensure_object(dict)
    ctx.obj["auth_token"] = auth_token
    ctx.obj["ct0"] = ct0
    ctx.obj["timeout"] = timeout / 1000 if timeout else None
    ctx.obj["rate_limit"] = max(0.0, rate_limit or 0.0)
    ctx.obj["as_json"] = as_json
    ctx.obj["quote_depth"] = quote_depth
    # plain implies both no_emoji and no_color
    ctx.obj["plain"] = plain or no_emoji or no_color
    # Respect NO_COLOR env var
    if os.environ.get("NO_COLOR"):
        ctx.obj["plain"] = True


def _client(ctx) -> TwitterClient:
    o = ctx.obj
    return _make_client(o["auth_token"], o["ct0"], o["timeout"], o.get("rate_limit", 0.0))


def _parse_since(value: str) -> datetime:
    """Parse --since (YYYY-MM-DD or ISO 8601) into an aware UTC datetime."""
    s = value.strip()
    try:
        if len(s) == 10 and s.count("-") == 2:
            dt = datetime.strptime(s, "%Y-%m-%d")
        else:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as exc:
        raise click.BadParameter(
            f"Invalid date {value!r}. Use YYYY-MM-DD or ISO 8601."
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

import html as _html

_SEPARATOR = "\u2500" * 50  # ──────────────────────────────────────────────────


def _unescape(text: str) -> str:
    return _html.unescape(text)


def _format_tweet(tweet, plain: bool = False, show_stats: bool = False) -> str:
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
                if plain:
                    tag = "[video]" if m.type in ("video", "animated_gif") else "[image]"
                    lines.append(f"\u2502 {tag} {m.url}")
                else:
                    icon = "\U0001f3ac" if m.type in ("video", "animated_gif") else "\U0001f5bc\ufe0f"
                    lines.append(f"\u2502 {icon} {m.url}")
        lines.append(f"\u2514\u2500 https://x.com/{qt.author.username}/status/{qt.id}")

    # Media on the outer tweet
    if tweet.media:
        for m in tweet.media:
            if plain:
                tag = "[video]" if m.type in ("video", "animated_gif") else "[image]"
                lines.append(f"{tag} {m.url}")
            else:
                icon = "\U0001f3ac" if m.type in ("video", "animated_gif") else "\U0001f5bc\ufe0f"
                lines.append(f"{icon} {m.url}")

    # Metadata
    if tweet.created_at:
        if plain:
            lines.append(f"date: {tweet.created_at}")
        else:
            lines.append(f"\U0001f4c5 {tweet.created_at}")
    url = f"https://x.com/{tweet.author.username}/status/{tweet.id}"
    if plain:
        lines.append(f"url: {url}")
    else:
        lines.append(f"\U0001f517 {url}")
    if tweet.author.profile_image_url:
        if plain:
            lines.append(f"avatar: {tweet.author.profile_image_url}")
        else:
            lines.append(f"\U0001f464 {tweet.author.profile_image_url}")

    # Engagement stats (shown for single-tweet read, not list views)
    if show_stats and not plain:
        parts = []
        if tweet.like_count is not None:
            parts.append(f"\u2764\ufe0f {tweet.like_count}")
        if tweet.retweet_count is not None:
            parts.append(f"\U0001f501 {tweet.retweet_count}")
        if tweet.reply_count is not None:
            parts.append(f"\U0001f4ac {tweet.reply_count}")
        if tweet.view_count is not None:
            parts.append(f"\U0001f441\ufe0f {tweet.view_count}")
        if parts:
            lines.append("  ".join(parts))
    else:
        if tweet.view_count is not None:
            if plain:
                lines.append(f"views: {tweet.view_count}")
            else:
                lines.append(f"\U0001f441\ufe0f {tweet.view_count} views")
        lines.append(_SEPARATOR)

    return "\n".join(lines)


def _dump_tweet(tweet, as_json: bool, plain: bool = False, include_raw: bool = False,
                show_stats: bool = False) -> None:
    if as_json:
        click.echo(json.dumps(_tweet_to_dict(tweet, include_raw=include_raw), ensure_ascii=False, indent=2))
    else:
        click.echo(_format_tweet(tweet, plain=plain, show_stats=show_stats))


def _dump_tweets(tweets, as_json: bool, plain: bool = False, include_raw: bool = False) -> None:
    if as_json:
        click.echo(json.dumps([_tweet_to_dict(t, include_raw=include_raw) for t in tweets], ensure_ascii=False))
    else:
        for t in tweets:
            click.echo(_format_tweet(t, plain=plain))


def _tweet_to_dict(tweet, include_raw: bool = False) -> dict:
    d: dict = {
        "id": tweet.id,
        "text": _unescape(tweet.text),
        "createdAt": tweet.created_at,
        "replyCount": tweet.reply_count,
        "retweetCount": tweet.retweet_count,
        "likeCount": tweet.like_count,
        "viewCount": tweet.view_count,
        "conversationId": tweet.conversation_id,
    }
    if tweet.in_reply_to_status_id:
        d["inReplyToStatusId"] = tweet.in_reply_to_status_id
    d["author"] = {
        "username": tweet.author.username,
        "name": tweet.author.name,
        "profileImageUrl": tweet.author.profile_image_url,
    }
    d["authorId"] = tweet.author_id
    if tweet.quoted_tweet:
        d["quotedTweet"] = _tweet_to_dict(tweet.quoted_tweet, include_raw=include_raw)
    if tweet.media:
        d["media"] = [_media_to_dict(m) for m in tweet.media]
    if include_raw and tweet._raw is not None:
        d["_raw"] = tweet._raw
    return d


def _media_to_dict(m) -> dict:
    d: dict = {"type": m.type, "url": m.url}
    if m.width is not None:
        d["width"] = m.width
    if m.height is not None:
        d["height"] = m.height
    if m.preview_url is not None:
        d["previewUrl"] = m.preview_url
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
@click.option("--json-full", "json_full", is_flag=True,
              help="Include raw API response in _raw field.")
@click.pass_context
def read(ctx, tweet_id_or_url, as_json, json_full):
    """Fetch and display a tweet by ID or URL."""
    tweet_id = extract_tweet_id(tweet_id_or_url)
    if not tweet_id:
        click.echo(f"Error: cannot parse tweet ID from {tweet_id_or_url!r}", err=True)
        sys.exit(1)
    as_json = as_json or json_full or ctx.obj.get("as_json")
    plain = ctx.obj.get("plain", False)
    with _client(ctx) as client:
        tweet = client.get_tweet(tweet_id, include_raw=json_full)
    if not tweet:
        click.echo("Tweet not found.", err=True)
        sys.exit(1)
    _dump_tweet(tweet, as_json, plain=plain, include_raw=json_full, show_stats=True)


# ---------------------------------------------------------------------------
# thread / replies
# ---------------------------------------------------------------------------

@main.command()
@click.argument("tweet_id_or_url")
@click.option("--json", "as_json", is_flag=True)
@click.option("--json-full", "json_full", is_flag=True,
              help="Include raw API response in _raw field.")
@click.pass_context
def thread(ctx, tweet_id_or_url, as_json, json_full):
    """Show the full conversation thread for a tweet."""
    tweet_id = extract_tweet_id(tweet_id_or_url)
    if not tweet_id:
        click.echo(f"Error: cannot parse tweet ID from {tweet_id_or_url!r}", err=True)
        sys.exit(1)
    as_json = as_json or json_full or ctx.obj.get("as_json")
    plain = ctx.obj.get("plain", False)
    with _client(ctx) as client:
        tweets = client.get_thread(tweet_id, include_raw=json_full)
    _dump_tweets(tweets, as_json, plain=plain, include_raw=json_full)


@main.command()
@click.argument("tweet_id_or_url")
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--json-full", "json_full", is_flag=True,
              help="Include raw API response in _raw field.")
@click.pass_context
def replies(ctx, tweet_id_or_url, count, as_json, json_full):
    """List replies to a tweet."""
    tweet_id = extract_tweet_id(tweet_id_or_url)
    if not tweet_id:
        click.echo(f"Error: cannot parse tweet ID from {tweet_id_or_url!r}", err=True)
        sys.exit(1)
    as_json = as_json or json_full or ctx.obj.get("as_json")
    plain = ctx.obj.get("plain", False)
    with _client(ctx) as client:
        tweets = client.get_replies(tweet_id, include_raw=json_full)
    _dump_tweets(tweets[:count], as_json, plain=plain, include_raw=json_full)


# ---------------------------------------------------------------------------
# tweet / reply
# ---------------------------------------------------------------------------

@main.command(name="tweet")
@click.argument("text")
@click.pass_context
def post_tweet(ctx, text):
    """Post a new tweet."""
    plain = ctx.obj.get("plain", False)
    try:
        with _client(ctx) as client:
            tweet_id = client.tweet(text)
    except RuntimeError as exc:
        click.echo(f"Failed to post tweet: {exc}", err=True)
        sys.exit(1)
    url = f"https://x.com/i/status/{tweet_id}"
    if plain:
        click.echo(f"Tweet posted successfully!\n{url}")
    else:
        click.echo(f"\u2705 Tweet posted successfully!\n\U0001f517 {url}")


@main.command(name="reply")
@click.argument("tweet_id_or_url")
@click.argument("text")
@click.pass_context
def post_reply(ctx, tweet_id_or_url, text):
    """Reply to a tweet."""
    plain = ctx.obj.get("plain", False)
    tweet_id = extract_tweet_id(tweet_id_or_url)
    if not tweet_id:
        click.echo(f"Error: cannot parse tweet ID from {tweet_id_or_url!r}", err=True)
        sys.exit(1)
    try:
        with _client(ctx) as client:
            new_id = client.reply(text, tweet_id)
    except RuntimeError as exc:
        click.echo(f"Failed to post reply: {exc}", err=True)
        sys.exit(1)
    url = f"https://x.com/i/status/{new_id}"
    if plain:
        click.echo(f"Reply posted successfully!\n{url}")
    else:
        click.echo(f"\u2705 Reply posted successfully!\n\U0001f517 {url}")


# ---------------------------------------------------------------------------
# search / mentions
# ---------------------------------------------------------------------------

@main.command()
@click.argument("query")
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--json-full", "json_full", is_flag=True,
              help="Include raw API response in _raw field.")
@click.option("--cursor", default=None)
@click.option("--max-pages", type=int, default=None)
@click.pass_context
def search(ctx, query, count, as_json, json_full, cursor, max_pages):
    """Search for tweets matching a query."""
    as_json = as_json or json_full or ctx.obj.get("as_json")
    plain = ctx.obj.get("plain", False)
    with _client(ctx) as client:
        tweets, next_cursor = client.search(query, count, cursor=cursor, max_pages=max_pages,
                                             include_raw=json_full)
    if as_json:
        click.echo(json.dumps([_tweet_to_dict(t, include_raw=json_full) for t in tweets],
                               ensure_ascii=False, indent=2))
    else:
        _dump_tweets(tweets, False, plain=plain)


@main.command()
@click.option("-u", "--user", default=None, help="@handle to search mentions for")
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--json-full", "json_full", is_flag=True,
              help="Include raw API response in _raw field.")
@click.pass_context
def mentions(ctx, user, count, as_json, json_full):
    """Find tweets mentioning a user (defaults to authenticated user)."""
    as_json = as_json or json_full or ctx.obj.get("as_json")
    plain = ctx.obj.get("plain", False)
    with _client(ctx) as client:
        tweets, _ = client.get_mentions(user, count, include_raw=json_full)
    _dump_tweets(tweets, as_json, plain=plain, include_raw=json_full)


# ---------------------------------------------------------------------------
# user-tweets
# ---------------------------------------------------------------------------

@main.command("user-tweets")
@click.argument("handle")
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--json-full", "json_full", is_flag=True,
              help="Include raw API response in _raw field.")
@click.option("--cursor", default=None)
@click.option("--max-pages", type=int, default=None)
@click.option("--since", "since", default=None,
              help="Fetch tweets back to this date (YYYY-MM-DD or ISO 8601) instead "
                   "of a fixed count. -n caps the result; --max-pages bounds requests.")
@click.option("--delay", "delay_ms", type=int, default=1000, show_default=True,
              help="Delay in ms between page fetches when paginating.")
@click.pass_context
def user_tweets(ctx, handle, count, as_json, json_full, cursor, max_pages, since, delay_ms):
    """Get tweets from a user's profile timeline."""
    as_json = as_json or json_full or ctx.obj.get("as_json")
    plain = ctx.obj.get("plain", False)
    norm = normalize_handle(handle)
    if not norm:
        click.echo(f"Invalid handle: {handle!r}", err=True)
        sys.exit(1)
    since_dt = _parse_since(since) if since else None
    # In since-mode, a default (unset) -n shouldn't cap results; an explicit -n does.
    if since_dt is not None and ctx.get_parameter_source("count") != click.core.ParameterSource.COMMANDLINE:
        count = None
    with _client(ctx) as client:
        user = client.get_user_id_by_username(norm)
        if not user:
            click.echo(f"User @{norm} not found.", err=True)
            sys.exit(1)
        tweets, next_cursor = client.get_user_tweets(
            user.id, count, cursor=cursor, max_pages=max_pages,
            include_raw=json_full, page_delay=delay_ms / 1000, since=since_dt,
        )
    if as_json:
        click.echo(json.dumps([_tweet_to_dict(t, include_raw=json_full) for t in tweets],
                               ensure_ascii=False, indent=2))
    else:
        _dump_tweets(tweets, False, plain=plain)


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
@click.option("--json-full", "json_full", is_flag=True,
              help="Include raw API response in _raw field.")
@click.pass_context
def bookmarks(ctx, count, folder_id, fetch_all, max_pages, cursor, as_json, json_full):
    """List bookmarked tweets."""
    as_json = as_json or json_full or ctx.obj.get("as_json")
    plain = ctx.obj.get("plain", False)
    limit = -1 if fetch_all else count
    with _client(ctx) as client:
        tweets, next_cursor = client.get_bookmarks(
            limit, folder_id=folder_id, cursor=cursor, max_pages=max_pages,
            include_raw=json_full,
        )
    if as_json:
        click.echo(json.dumps([_tweet_to_dict(t, include_raw=json_full) for t in tweets],
                               ensure_ascii=False, indent=2))
    else:
        _dump_tweets(tweets, False, plain=plain)


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
@click.option("--json-full", "json_full", is_flag=True,
              help="Include raw API response in _raw field.")
@click.option("--cursor", default=None)
@click.pass_context
def likes(ctx, count, as_json, json_full, cursor):
    """List liked tweets."""
    as_json = as_json or json_full or ctx.obj.get("as_json")
    plain = ctx.obj.get("plain", False)
    with _client(ctx) as client:
        tweets, next_cursor = client.get_likes(count, cursor=cursor, include_raw=json_full)
    if as_json:
        click.echo(json.dumps([_tweet_to_dict(t, include_raw=json_full) for t in tweets],
                               ensure_ascii=False, indent=2))
    else:
        _dump_tweets(tweets, False, plain=plain)


# ---------------------------------------------------------------------------
# home
# ---------------------------------------------------------------------------

@main.command()
@click.option("-n", "--count", default=20, show_default=True)
@click.option("--following", is_flag=True, help="Show Following (chronological) feed")
@click.option("--json", "as_json", is_flag=True)
@click.option("--json-full", "json_full", is_flag=True,
              help="Include raw API response in _raw field.")
@click.pass_context
def home(ctx, count, following, as_json, json_full):
    """Fetch home timeline (For You or Following feed)."""
    as_json = as_json or json_full or ctx.obj.get("as_json")
    plain = ctx.obj.get("plain", False)
    with _client(ctx) as client:
        if following:
            tweets = client.get_home_latest_timeline(count)
        else:
            tweets = client.get_home_timeline(count)
    _dump_tweets(tweets, as_json, plain=plain, include_raw=json_full)


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
# follow / unfollow
# ---------------------------------------------------------------------------

def _resolve_user_id(client, username_or_id: str) -> Optional[str]:
    """Return a numeric user ID from a bare ID or @handle / handle."""
    val = username_or_id.lstrip("@").strip()
    if val.isdigit():
        return val
    norm = normalize_handle(val)
    if not norm:
        return None
    user = client.get_user_id_by_username(norm)
    return user.id if user else None


@main.command(name="follow")
@click.argument("username_or_id")
@click.pass_context
def follow_user(ctx, username_or_id):
    """Follow a user (username with or without @, or numeric user ID)."""
    with _client(ctx) as client:
        uid = _resolve_user_id(client, username_or_id)
        if not uid:
            click.echo(f"User not found: {username_or_id!r}", err=True)
            sys.exit(1)
        ok = client.follow(uid)
    if ok:
        click.echo(f"Followed: {username_or_id}")
    else:
        click.echo(f"Failed to follow: {username_or_id}", err=True)
        sys.exit(1)


@main.command(name="unfollow")
@click.argument("username_or_id")
@click.pass_context
def unfollow_user(ctx, username_or_id):
    """Unfollow a user (username with or without @, or numeric user ID)."""
    with _client(ctx) as client:
        uid = _resolve_user_id(client, username_or_id)
        if not uid:
            click.echo(f"User not found: {username_or_id!r}", err=True)
            sys.exit(1)
        ok = client.unfollow(uid)
    if ok:
        click.echo(f"Unfollowed: {username_or_id}")
    else:
        click.echo(f"Failed to unfollow: {username_or_id}", err=True)
        sys.exit(1)


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
@click.option("--json-full", "json_full", is_flag=True,
              help="Include raw API response in _raw field.")
@click.option("--cursor", default=None)
@click.option("--max-pages", type=int, default=None)
@click.pass_context
def list_timeline(ctx, list_id_or_url, count, as_json, json_full, cursor, max_pages):
    """Get tweets from a list timeline."""
    list_id = extract_list_id(list_id_or_url)
    if not list_id:
        click.echo(f"Cannot parse list ID from {list_id_or_url!r}", err=True)
        sys.exit(1)
    as_json = as_json or json_full or ctx.obj.get("as_json")
    plain = ctx.obj.get("plain", False)
    with _client(ctx) as client:
        tweets, next_cursor = client.get_list_timeline(list_id, count, cursor=cursor,
                                                        max_pages=max_pages, include_raw=json_full)
    if as_json:
        click.echo(json.dumps([_tweet_to_dict(t, include_raw=json_full) for t in tweets],
                               ensure_ascii=False, indent=2))
    else:
        _dump_tweets(tweets, False, plain=plain)


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
    plain = ctx.obj.get("plain", False)
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
# user / about / whoami / check
# ---------------------------------------------------------------------------

def _profile_to_dict(p, include_raw: bool = False) -> dict:
    d: dict = {
        "id": p.id,
        "username": p.username,
        "name": p.name,
        "description": p.description,
        "location": p.location,
        "website": p.website,
        "createdAt": p.created_at,
        "followersCount": p.followers_count,
        "followingCount": p.following_count,
        "tweetCount": p.tweet_count,
        "mediaCount": p.media_count,
        "listedCount": p.listed_count,
        "likesCount": p.likes_count,
        "isBlueVerified": p.is_blue_verified,
        "isVerified": p.is_verified,
        "verifiedType": p.verified_type,
        "isIdentityVerified": p.is_identity_verified,
        "verifiedSince": p.verified_since,
        "profileImageUrl": p.profile_image_url,
        "profileBannerUrl": p.profile_banner_url,
        "isProtected": p.is_protected,
        "canDm": p.can_dm,
        "pinnedTweetIds": p.pinned_tweet_ids,
        "professionalType": p.professional_type,
        "professionalCategory": p.professional_category,
    }
    if include_raw and p._raw is not None:
        d["_raw"] = p._raw
    return d


def _format_profile(p, plain: bool = False) -> str:
    def line(emoji: str, label: str, value) -> str:
        return f"{label}: {value}" if plain else f"{emoji} {value}"

    lines = [f"@{p.username} ({p.name})"]
    if p.description:
        lines.append(_unescape(p.description))
    if p.location:
        lines.append(line("\U0001f4cd", "location", p.location))
    if p.website:
        lines.append(line("\U0001f517", "website", p.website))
    if p.created_at:
        lines.append(line("\U0001f4c5", "joined", p.created_at))

    counts = []
    if p.followers_count is not None:
        counts.append(f"{p.followers_count:,} followers")
    if p.following_count is not None:
        counts.append(f"{p.following_count:,} following")
    if p.tweet_count is not None:
        counts.append(f"{p.tweet_count:,} tweets")
    if p.media_count is not None:
        counts.append(f"{p.media_count:,} media")
    if p.likes_count is not None:
        counts.append(f"{p.likes_count:,} likes")
    if p.listed_count is not None:
        counts.append(f"{p.listed_count:,} listed")
    if counts:
        lines.append(line("\U0001f465", "stats", " · ".join(counts)))

    badges = []
    if p.verified_type:
        badges.append(f"{p.verified_type} verified")
    elif p.is_verified:
        badges.append("verified")
    if p.is_blue_verified:
        badges.append("blue check")
    if p.is_identity_verified:
        badges.append("identity verified")
    if p.is_protected:
        badges.append("protected")
    if badges:
        lines.append(line("✅", "verified", ", ".join(badges)))
    if p.verified_since:
        lines.append(line("\U0001f4ce", "verified since", p.verified_since))
    if p.professional_type:
        prof = p.professional_type
        if p.professional_category:
            prof += f" ({p.professional_category})"
        lines.append(line("\U0001f4bc", "professional", prof))
    if p.can_dm is not None:
        dm = "open" if p.can_dm else "closed"
        lines.append(f"dms: {dm}" if plain else f"\U0001f4e9 DMs {dm}")
    if p.pinned_tweet_ids:
        lines.append(line("\U0001f4cc", "pinned", ", ".join(p.pinned_tweet_ids)))
    if p.profile_image_url:
        lines.append(f"avatar: {p.profile_image_url}" if plain
                     else f"\U0001f464 {p.profile_image_url}")
    if p.profile_banner_url:
        lines.append(f"banner: {p.profile_banner_url}" if plain
                     else f"\U0001f5bc\ufe0f {p.profile_banner_url}")
    lines.append(f"id: {p.id}")
    return "\n".join(lines)


@main.command("user")
@click.argument("handle")
@click.option("--json", "as_json", is_flag=True)
@click.option("--json-full", "json_full", is_flag=True,
              help="Include raw API response in _raw field.")
@click.pass_context
def user_profile(ctx, handle, as_json, json_full):
    """Show full profile information for a user."""
    as_json = as_json or json_full or ctx.obj.get("as_json")
    plain = ctx.obj.get("plain", False)
    norm = normalize_handle(handle)
    if not norm:
        click.echo(f"Invalid handle: {handle!r}", err=True)
        sys.exit(1)
    with _client(ctx) as client:
        profile = client.get_user_profile(norm, include_raw=json_full)
    if not profile:
        click.echo(f"User @{norm} not found.", err=True)
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(_profile_to_dict(profile, include_raw=json_full),
                               ensure_ascii=False, indent=2))
    else:
        click.echo(_format_profile(profile, plain=plain))

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
        if profile.learn_more_url:
            click.echo(f"Info: {profile.learn_more_url}")


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
