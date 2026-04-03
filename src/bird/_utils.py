"""Tweet/user parsing utilities, ported from twitter-client-utils.js."""

from __future__ import annotations

import re
from typing import Any, Optional

from ._models import (
    ArticleMetadata,
    Author,
    MediaItem,
    Tweet,
    User,
)


# ---------------------------------------------------------------------------
# Handle normalisation
# ---------------------------------------------------------------------------

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def normalize_handle(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = raw.strip().lstrip("@").strip()
    if not s or not _HANDLE_RE.match(s):
        return None
    return s


# ---------------------------------------------------------------------------
# URL extraction helpers
# ---------------------------------------------------------------------------

_TWEET_ID_RE = re.compile(r"/status(?:es)?/(\d+)")
_LIST_ID_RE = re.compile(r"/lists?/(\d+)")
_BOOKMARK_FOLDER_ID_RE = re.compile(r"/bookmarks/(\d+)")


def extract_tweet_id(value: str) -> Optional[str]:
    """Return tweet ID from a URL or bare numeric ID."""
    if value.isdigit():
        return value
    m = _TWEET_ID_RE.search(value)
    return m.group(1) if m else None


def extract_list_id(value: str) -> Optional[str]:
    if value.isdigit():
        return value
    m = _LIST_ID_RE.search(value)
    return m.group(1) if m else None


def extract_bookmark_folder_id(value: str) -> Optional[str]:
    if value.isdigit():
        return value
    m = _BOOKMARK_FOLDER_ID_RE.search(value)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Draft.js / rich-content rendering
# ---------------------------------------------------------------------------

def _render_block_text(block: dict, entity_map: dict[int, Any]) -> str:
    text: str = block.get("text", "")
    # Apply LINK entities in reverse offset order to preserve positions
    link_ranges = [
        r for r in block.get("entityRanges", [])
        if entity_map.get(r["key"], {}).get("type") == "LINK"
        and entity_map[r["key"]].get("data", {}).get("url")
    ]
    link_ranges.sort(key=lambda r: r["offset"], reverse=True)
    for rng in link_ranges:
        entity = entity_map[rng["key"]]
        url = entity["data"]["url"]
        start, length = rng["offset"], rng["length"]
        link_text = text[start : start + length]
        text = text[:start] + f"[{link_text}]({url})" + text[start + length:]
    return text.strip()


def _render_atomic_block(block: dict, entity_map: dict[int, Any]) -> Optional[str]:
    ranges = block.get("entityRanges", [])
    if not ranges:
        return None
    entity = entity_map.get(ranges[0]["key"])
    if not entity:
        return None
    t = entity.get("type")
    data = entity.get("data", {})
    if t == "MARKDOWN":
        md = data.get("markdown", "")
        return md.strip() if md else None
    if t == "DIVIDER":
        return "---"
    if t == "TWEET" and data.get("tweetId"):
        return f"[Embedded Tweet: https://x.com/i/status/{data['tweetId']}]"
    if t == "LINK" and data.get("url"):
        return f"[Link: {data['url']}]"
    if t == "IMAGE":
        return "[Image]"
    return None


def render_content_state(content_state: Optional[dict]) -> Optional[str]:
    """Render a Draft.js content_state to Markdown-like plain text."""
    if not content_state or not content_state.get("blocks"):
        return None

    # Build entity lookup
    entity_map: dict[int, Any] = {}
    raw_map = content_state.get("entityMap", [])
    if isinstance(raw_map, list):
        for entry in raw_map:
            try:
                entity_map[int(entry["key"])] = entry["value"]
            except (KeyError, ValueError):
                pass
    elif isinstance(raw_map, dict):
        for k, v in raw_map.items():
            try:
                entity_map[int(k)] = v
            except ValueError:
                pass

    lines: list[str] = []
    ordered_counter = 0
    prev_type: Optional[str] = None

    for block in content_state["blocks"]:
        btype = block.get("type", "unstyled")
        if btype != "ordered-list-item" and prev_type == "ordered-list-item":
            ordered_counter = 0

        if btype == "unstyled":
            text = _render_block_text(block, entity_map)
            if text:
                lines.append(text)
        elif btype == "header-one":
            text = _render_block_text(block, entity_map)
            if text:
                lines.append(f"# {text}")
        elif btype == "header-two":
            text = _render_block_text(block, entity_map)
            if text:
                lines.append(f"## {text}")
        elif btype == "header-three":
            text = _render_block_text(block, entity_map)
            if text:
                lines.append(f"### {text}")
        elif btype == "unordered-list-item":
            text = _render_block_text(block, entity_map)
            if text:
                lines.append(f"- {text}")
        elif btype == "ordered-list-item":
            ordered_counter += 1
            text = _render_block_text(block, entity_map)
            if text:
                lines.append(f"{ordered_counter}. {text}")
        elif btype == "blockquote":
            text = _render_block_text(block, entity_map)
            if text:
                lines.append(f"> {text}")
        elif btype == "atomic":
            content = _render_atomic_block(block, entity_map)
            if content:
                lines.append(content)
        else:
            text = _render_block_text(block, entity_map)
            if text:
                lines.append(text)

        prev_type = btype

    result = "\n\n".join(lines).strip()
    return result or None


# ---------------------------------------------------------------------------
# Tweet text extraction
# ---------------------------------------------------------------------------

def _first_text(*values: Any) -> Optional[str]:
    for v in values:
        if isinstance(v, str):
            s = v.strip()
            if s:
                return s
    return None


def _collect_text_fields(value: Any, keys: set[str], output: list[str]) -> None:
    if not value or isinstance(value, str):
        return
    if isinstance(value, list):
        for item in value:
            _collect_text_fields(item, keys, output)
        return
    if isinstance(value, dict):
        for k, nested in value.items():
            if k in keys and isinstance(nested, str):
                s = nested.strip()
                if s:
                    output.append(s)
                continue
            _collect_text_fields(nested, keys, output)


def _unique_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            result.append(v)
    return result


def _extract_article_text(result: dict) -> Optional[str]:
    article = result.get("article")
    if not article:
        return None
    article_result = article.get("article_results", {}).get("result") or article
    title = _first_text(article_result.get("title"), article.get("title"))
    content_state = article.get("article_results", {}).get("result", {}).get("content_state")
    rich_body = render_content_state(content_state)
    if rich_body:
        if title:
            nt = title.strip()
            tb = rich_body.lstrip()
            headings = [f"# {nt}", f"## {nt}", f"### {nt}"]
            has_title = (
                tb == nt
                or tb.startswith(nt + "\n")
                or any(tb.startswith(h) for h in headings)
            )
            if not has_title:
                return f"{title}\n\n{rich_body}"
        return rich_body

    # Fallback: plain text
    body = _first_text(
        article_result.get("plain_text"),
        article.get("plain_text"),
        (article_result.get("body") or {}).get("text"),
        (article_result.get("body") or {}).get("richtext", {}).get("text"),
        (article_result.get("body") or {}).get("rich_text", {}).get("text"),
        (article_result.get("content") or {}).get("text"),
        article_result.get("text"),
    )
    if body and title and body.strip() == title.strip():
        body = None
    if not body:
        collected: list[str] = []
        _collect_text_fields(article_result, {"text", "title"}, collected)
        _collect_text_fields(article, {"text", "title"}, collected)
        unique = _unique_ordered(collected)
        filtered = [v for v in unique if v != title] if title else unique
        if filtered:
            body = "\n\n".join(filtered)
    if title and body and not body.startswith(title):
        return f"{title}\n\n{body}"
    return body or title


def _extract_note_tweet_text(result: dict) -> Optional[str]:
    note = (result.get("note_tweet") or {}).get("note_tweet_results", {}).get("result")
    if not note:
        return None
    return _first_text(
        note.get("text"),
        (note.get("richtext") or {}).get("text"),
        (note.get("rich_text") or {}).get("text"),
        (note.get("content") or {}).get("text"),
    )


def _extract_tweet_text(result: dict) -> Optional[str]:
    return (
        _extract_article_text(result)
        or _extract_note_tweet_text(result)
        or _first_text((result.get("legacy") or {}).get("full_text"))
    )


def _extract_article_metadata(result: dict) -> Optional[ArticleMetadata]:
    article = result.get("article")
    if not article:
        return None
    article_result = article.get("article_results", {}).get("result") or article
    title = _first_text(article_result.get("title"), article.get("title"))
    if not title:
        return None
    preview_text = _first_text(
        article_result.get("preview_text"), article.get("preview_text")
    )
    return ArticleMetadata(title=title, preview_text=preview_text)


def _extract_media(result: dict) -> Optional[list[MediaItem]]:
    legacy = result.get("legacy") or {}
    raw_media = (legacy.get("extended_entities") or {}).get("media") or (
        legacy.get("entities") or {}
    ).get("media")
    if not raw_media:
        return None
    items: list[MediaItem] = []
    for item in raw_media:
        if not item.get("type") or not item.get("media_url_https"):
            continue
        media_item = MediaItem(type=item["type"], url=item["media_url_https"])
        sizes = item.get("sizes") or {}
        for size_key in ("large", "medium"):
            sz = sizes.get(size_key)
            if sz:
                media_item.width = sz.get("w")
                media_item.height = sz.get("h")
                break
        if sizes.get("small"):
            media_item.preview_url = f"{item['media_url_https']}:small"
        if item["type"] in ("video", "animated_gif"):
            video_info = item.get("video_info") or {}
            variants = video_info.get("variants") or []
            mp4 = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
            mp4_with_bitrate = sorted(
                [v for v in mp4 if isinstance(v.get("bitrate"), int)],
                key=lambda v: v["bitrate"],
                reverse=True,
            )
            selected = (mp4_with_bitrate or mp4 or [None])[0]
            if selected:
                media_item.video_url = selected["url"]
            if isinstance(video_info.get("duration_millis"), int):
                media_item.duration_ms = video_info["duration_millis"]
        items.append(media_item)
    return items or None


def _unwrap_tweet_result(result: Optional[dict]) -> Optional[dict]:
    if not result:
        return None
    return result.get("tweet") or result


def map_tweet_result(
    result: Optional[dict],
    quote_depth: int = 1,
    include_raw: bool = False,
) -> Optional[Tweet]:
    if not result:
        return None
    user_result = (result.get("core") or {}).get("user_results", {}).get("result") or {}
    user_legacy = user_result.get("legacy") or {}
    user_core = user_result.get("core") or {}
    username = user_legacy.get("screen_name") or user_core.get("screen_name")
    name = user_legacy.get("name") or user_core.get("name") or username
    user_id = user_result.get("rest_id")

    if not result.get("rest_id") or not username:
        return None

    text = _extract_tweet_text(result)
    if not text:
        return None

    quoted_tweet: Optional[Tweet] = None
    if quote_depth > 0:
        quoted_result = _unwrap_tweet_result(
            (result.get("quoted_status_result") or {}).get("result")
        )
        if quoted_result:
            quoted_tweet = map_tweet_result(quoted_result, quote_depth - 1, include_raw)

    legacy = result.get("legacy") or {}
    tweet = Tweet(
        id=result["rest_id"],
        text=text,
        author=Author(username=username, name=name or username),
        created_at=legacy.get("created_at"),
        reply_count=legacy.get("reply_count"),
        retweet_count=legacy.get("retweet_count"),
        like_count=legacy.get("favorite_count"),
        conversation_id=legacy.get("conversation_id_str"),
        in_reply_to_status_id=legacy.get("in_reply_to_status_id_str") or None,
        author_id=user_id,
        quoted_tweet=quoted_tweet,
        media=_extract_media(result),
        article=_extract_article_metadata(result),
    )
    if include_raw:
        tweet._raw = result
    return tweet


def _collect_tweet_results_from_entry(entry: dict) -> list[dict]:
    results: list[dict] = []
    content = entry.get("content") or {}

    def push(r: Optional[dict]) -> None:
        if r and r.get("rest_id"):
            results.append(r)

    push((content.get("itemContent") or {}).get("tweet_results", {}).get("result"))
    push((content.get("item") or {}).get("itemContent", {}).get("tweet_results", {}).get("result"))
    for item in content.get("items") or []:
        push((item.get("item") or {}).get("itemContent", {}).get("tweet_results", {}).get("result"))
        push((item.get("itemContent") or {}).get("tweet_results", {}).get("result"))
        push((item.get("content") or {}).get("itemContent", {}).get("tweet_results", {}).get("result"))
    return results


def parse_tweets_from_instructions(
    instructions: Optional[list],
    quote_depth: int = 1,
    include_raw: bool = False,
) -> list[Tweet]:
    tweets: list[Tweet] = []
    seen: set[str] = set()
    for instruction in instructions or []:
        for entry in instruction.get("entries") or []:
            for result in _collect_tweet_results_from_entry(entry):
                mapped = map_tweet_result(result, quote_depth, include_raw)
                if mapped and mapped.id not in seen:
                    seen.add(mapped.id)
                    tweets.append(mapped)
    return tweets


def extract_cursor_from_instructions(
    instructions: Optional[list],
    cursor_type: str = "Bottom",
) -> Optional[str]:
    for instruction in instructions or []:
        for entry in instruction.get("entries") or []:
            content = entry.get("content") or {}
            if content.get("cursorType") == cursor_type and content.get("value"):
                return content["value"]
    return None


def find_tweet_in_instructions(
    instructions: Optional[list],
    tweet_id: str,
) -> Optional[dict]:
    for instruction in instructions or []:
        for entry in instruction.get("entries") or []:
            result = (entry.get("content") or {}).get("itemContent", {}).get(
                "tweet_results", {}
            ).get("result")
            if result and result.get("rest_id") == tweet_id:
                return result
    return None


def parse_users_from_instructions(instructions: Optional[list]) -> list[User]:
    users: list[User] = []
    for instruction in instructions or []:
        for entry in instruction.get("entries") or []:
            content = entry.get("content") or {}
            raw = (content.get("itemContent") or {}).get("user_results", {}).get("result")
            if not raw:
                continue
            # Unwrap UserWithVisibilityResults
            if raw.get("__typename") == "UserWithVisibilityResults" and raw.get("user"):
                raw = raw["user"]
            if raw.get("__typename") != "User":
                continue
            legacy = raw.get("legacy") or {}
            core = raw.get("core") or {}
            username = legacy.get("screen_name") or core.get("screen_name")
            if not raw.get("rest_id") or not username:
                continue
            users.append(
                User(
                    id=raw["rest_id"],
                    username=username,
                    name=legacy.get("name") or core.get("name") or username,
                    description=legacy.get("description"),
                    followers_count=legacy.get("followers_count"),
                    following_count=legacy.get("friends_count"),
                    is_blue_verified=raw.get("is_blue_verified"),
                    profile_image_url=legacy.get("profile_image_url_https")
                    or (raw.get("avatar") or {}).get("image_url"),
                    created_at=legacy.get("created_at") or core.get("created_at"),
                )
            )
    return users
