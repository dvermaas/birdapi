"""TwitterClient — synchronous X/Twitter GraphQL client.

Authentication requires two cookies from an active X/Twitter web session:
  - auth_token   (the session token)
  - ct0          (the CSRF token)

These can be copied from browser DevTools → Application → Cookies → x.com.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from typing import Any, Optional

import httpx

from ._constants import (
    BEARER_TOKEN,
    FALLBACK_QUERY_IDS,
    SETTINGS_NAME_RE,
    SETTINGS_SCREEN_NAME_RE,
    SETTINGS_USER_ID_RE,
    TWITTER_API_BASE,
    TWITTER_STATUS_UPDATE_URL,
)
from ._features import (
    article_field_toggles,
    bookmarks_features,
    explore_features,
    following_features,
    home_timeline_features,
    likes_features,
    lists_features,
    search_features,
    tweet_create_features,
    tweet_detail_features,
    user_tweets_features,
)
from ._models import (
    AboutProfile,
    NewsItem,
    Tweet,
    TwitterList,
    User,
)
from ._query_ids import query_id_store
from ._utils import (
    _extract_article_text,  # noqa: PLC2701 — internal helper
    _first_text,            # noqa: PLC2701
    extract_cursor_from_instructions,
    find_tweet_in_instructions,
    map_tweet_result,
    normalize_handle,
    parse_tweets_from_instructions,
    parse_users_from_instructions,
)

_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_PAGE_SIZE = 20

# Regex to detect query-ID mismatch errors in 400/422 responses
_RAW_QUERY_MISSING_RE = re.compile(r"must be defined", re.IGNORECASE)
_QUERY_UNSPECIFIED_RE = re.compile(r"query:\s*unspecified", re.IGNORECASE)

# Explore tab timeline IDs (base64-encoded internal identifiers)
_TIMELINE_IDS = {
    "forYou": "VGltZWxpbmU6DAC2CwABAAAAB2Zvcl95b3UAAA==",
    "trending": "VGltZWxpbmU6DAC2CwABAAAACHRyZW5kaW5nAAA=",
    "news": "VGltZWxpbmU6DAC2CwABAAAABG5ld3MAAA==",
    "sports": "VGltZWxpbmU6DAC2CwABAAAABnNwb3J0cwAA",
    "entertainment": "VGltZWxpbmU6DAC2CwABAAAADWVudGVydGFpbm1lbnQAAA==",
}

_POST_COUNT_RE = re.compile(r"[\d.]+[KMB]?\s*posts?", re.IGNORECASE)
_POST_COUNT_MATCH_RE = re.compile(r"([\d.]+)([KMB]?)\s*posts?", re.IGNORECASE)


def _parse_post_count(text: str) -> Optional[int]:
    m = _POST_COUNT_MATCH_RE.search(text)
    if not m:
        return None
    num = float(m.group(1))
    suffix = (m.group(2) or "").upper()
    if suffix == "K":
        num *= 1_000
    elif suffix == "M":
        num *= 1_000_000
    elif suffix == "B":
        num *= 1_000_000_000
    return round(num)


class TwitterClient:
    """Synchronous client for X/Twitter's internal GraphQL API.

    Parameters
    ----------
    auth_token:
        Value of the ``auth_token`` cookie from an active X session.
    ct0:
        Value of the ``ct0`` cookie (CSRF token).
    cookie_header:
        Full ``Cookie`` header string.  When omitted it is constructed from
        *auth_token* and *ct0*.
    user_agent:
        Override the default browser user-agent string.
    timeout:
        HTTP request timeout in seconds (default: no timeout).
    quote_depth:
        How many levels of quoted tweets to include in responses (default 1).
    """

    def __init__(
        self,
        auth_token: str,
        ct0: str,
        *,
        cookie_header: Optional[str] = None,
        user_agent: str = _DEFAULT_UA,
        timeout: Optional[float] = None,
        quote_depth: int = 1,
    ) -> None:
        if not auth_token or not ct0:
            raise ValueError("Both auth_token and ct0 are required")
        self._auth_token = auth_token
        self._ct0 = ct0
        self._cookie = cookie_header or f"auth_token={auth_token}; ct0={ct0}"
        self._user_agent = user_agent
        self._timeout = timeout
        self._quote_depth = max(0, int(quote_depth))
        self._client_uuid = str(uuid.uuid4())
        self._client_device_id = str(uuid.uuid4())
        self._client_user_id: Optional[str] = None
        self._http = httpx.Client(timeout=self._timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "TwitterClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "authorization": f"Bearer {BEARER_TOKEN}",
            "x-csrf-token": self._ct0,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "x-client-uuid": self._client_uuid,
            "x-twitter-client-deviceid": self._client_device_id,
            "x-client-transaction-id": os.urandom(16).hex(),
            "cookie": self._cookie,
            "user-agent": self._user_agent,
            "origin": "https://x.com",
            "referer": "https://x.com/",
        }
        if self._client_user_id:
            headers["x-twitter-client-user-id"] = self._client_user_id
        return headers

    def _json_headers(self) -> dict[str, str]:
        return {**self._base_headers(), "content-type": "application/json"}

    def _get_query_id(self, operation: str) -> str:
        return query_id_store.get(operation) or FALLBACK_QUERY_IDS.get(operation, "")

    def _refresh_query_ids(self) -> None:
        if os.environ.get("BIRD_SKIP_QUERY_ID_REFRESH"):
            return
        query_id_store.refresh(list(FALLBACK_QUERY_IDS.keys()), force=True)

    def _tweet_detail_query_ids(self) -> list[str]:
        primary = self._get_query_id("TweetDetail")
        return list(dict.fromkeys([primary, "97JF30KziU00483E_8elBA", "aFvUsJm2c-oDkJV75blV6g"]))

    def _search_query_ids(self) -> list[str]:
        primary = self._get_query_id("SearchTimeline")
        return list(dict.fromkeys([primary, "M1jEez78PEfVfbQLvlWMvQ", "5h0kNbk3ii97rmfY6CdgAA"]))

    def _ensure_client_user_id(self) -> None:
        if self._client_user_id:
            return
        result = self.get_current_user()
        if result and result.id:
            self._client_user_id = result.id

    def _get(self, url: str) -> httpx.Response:
        return self._http.get(url, headers=self._json_headers())

    def _post(self, url: str, body: str) -> httpx.Response:
        return self._http.post(url, headers=self._json_headers(), content=body.encode())

    def _post_form(self, url: str, data: dict, extra_headers: Optional[dict] = None) -> httpx.Response:
        headers = {**self._base_headers(), "content-type": "application/x-www-form-urlencoded"}
        if extra_headers:
            headers.update(extra_headers)
        return self._http.post(url, headers=headers, data=data)

    # Retry logic for transient errors (used by bookmarks)
    def _get_with_retry(self, url: str, max_retries: int = 2) -> httpx.Response:
        retryable = {429, 500, 502, 503, 504}
        base_delay = 0.5
        for attempt in range(max_retries + 1):
            resp = self._get(url)
            if resp.status_code not in retryable or attempt == max_retries:
                return resp
            retry_after = resp.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                delay = int(retry_after)
            else:
                delay = base_delay * (2 ** attempt) + (time.monotonic() % base_delay)
            time.sleep(delay)
        return self._get(url)  # unreachable but satisfies type checker

    @staticmethod
    def _is_query_id_mismatch(payload: str) -> bool:
        try:
            data = json.loads(payload)
            errors = data.get("errors") or []
            return any(
                (e or {}).get("extensions", {}).get("code") == "GRAPHQL_VALIDATION_FAILED"
                or (
                    "rawQuery" in ((e or {}).get("path") or [])
                    and _RAW_QUERY_MISSING_RE.search((e or {}).get("message", ""))
                )
                for e in errors
            )
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Pagination helper
    # ------------------------------------------------------------------

    def _paginate(
        self,
        fetch_page,  # callable(cursor) -> (tweets, next_cursor, had_404, error)
        limit: int,
        max_pages: Optional[int] = None,
        initial_cursor: Optional[str] = None,
    ) -> tuple[list[Tweet], Optional[str], Optional[str]]:
        """Generic tweet pagination loop.

        Returns (tweets, next_cursor, error).
        """
        tweets: list[Tweet] = []
        seen: set[str] = set()
        cursor = initial_cursor
        next_cursor: Optional[str] = None
        pages_fetched = 0
        unlimited = limit == math.inf or limit < 0

        while unlimited or len(tweets) < limit:
            page_count = _PAGE_SIZE if unlimited else min(_PAGE_SIZE, limit - len(tweets))
            page_tweets, page_cursor, had_404, error = fetch_page(cursor, page_count)

            if error and not page_tweets:
                # Attempt query ID refresh on 404 / mismatch
                if had_404:
                    self._refresh_query_ids()
                    page_tweets, page_cursor, _, error = fetch_page(cursor, page_count)
                    if error and not page_tweets:
                        return tweets, None, error
                else:
                    return tweets, None, error

            pages_fetched += 1
            added = 0
            for t in page_tweets:
                if t.id in seen:
                    continue
                seen.add(t.id)
                tweets.append(t)
                added += 1
                if not unlimited and len(tweets) >= limit:
                    break

            if not page_cursor or page_cursor == cursor or not page_tweets or added == 0:
                next_cursor = None
                break
            if max_pages and pages_fetched >= max_pages:
                next_cursor = page_cursor
                break
            cursor = page_cursor
            next_cursor = page_cursor

        return tweets, next_cursor, None

    # ------------------------------------------------------------------
    # Current user
    # ------------------------------------------------------------------

    def get_current_user(self) -> Optional[User]:
        """Return the user associated with the current cookies."""
        candidate_urls = [
            "https://x.com/i/api/account/settings.json",
            "https://api.twitter.com/1.1/account/settings.json",
            "https://x.com/i/api/account/verify_credentials.json?skip_status=true&include_entities=false",
        ]
        for url in candidate_urls:
            try:
                r = self._get(url)
                if not r.is_success:
                    continue
                data = r.json()
                username = (
                    data.get("screen_name")
                    or (data.get("user") or {}).get("screen_name")
                )
                name = (
                    data.get("name")
                    or (data.get("user") or {}).get("name")
                    or username or ""
                )
                user_id = (
                    data.get("user_id")
                    or data.get("user_id_str")
                    or (data.get("user") or {}).get("id_str")
                    or (data.get("user") or {}).get("id")
                )
                if username and user_id:
                    self._client_user_id = str(user_id)
                    return User(id=str(user_id), username=username, name=name or username)
            except Exception:
                pass

        # Fallback: scrape settings HTML
        for page in ["https://x.com/settings/account", "https://twitter.com/settings/account"]:
            try:
                r = self._http.get(
                    page,
                    headers={"cookie": self._cookie, "user-agent": self._user_agent},
                )
                if not r.is_success:
                    continue
                html = r.text
                um = SETTINGS_SCREEN_NAME_RE.search(html)
                im = SETTINGS_USER_ID_RE.search(html)
                nm = SETTINGS_NAME_RE.search(html)
                if um and im:
                    name = nm.group(1).replace('\\"', '"') if nm else um.group(1)
                    return User(id=im.group(1), username=um.group(1), name=name)
            except Exception:
                pass
        return None

    # ------------------------------------------------------------------
    # User lookup
    # ------------------------------------------------------------------

    def get_user_id_by_username(self, username: str) -> Optional[User]:
        """Resolve a username/handle to a User (id, username, name)."""
        handle = normalize_handle(username)
        if not handle:
            return None

        # Try GraphQL UserByScreenName
        query_ids = [
            "xc8f1g7BYqr6VTzTbvNlGw",
            "qW5u-DAuXpMEG0zA1F7UGQ",
            "sLVLhk0bGj3MVFEKTdax1w",
        ]
        variables = {"screen_name": handle, "withSafetyModeUserFields": True}
        features = {
            "hidden_profile_subscriptions_enabled": True,
            "hidden_profile_likes_enabled": True,
            "rweb_tipjar_consumption_enabled": True,
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "subscriptions_verification_info_is_identity_verified_enabled": True,
            "subscriptions_verification_info_verified_since_enabled": True,
            "highlights_tweets_tab_ui_enabled": True,
            "responsive_web_twitter_article_notes_tab_enabled": True,
            "subscriptions_feature_can_gift_premium": True,
            "creator_subscriptions_tweet_preview_api_enabled": True,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "responsive_web_graphql_timeline_navigation_enabled": True,
            "blue_business_profile_image_shape_enabled": True,
        }
        params = httpx.QueryParams(
            variables=json.dumps(variables),
            features=json.dumps(features),
            fieldToggles=json.dumps({"withAuxiliaryUserLabels": False}),
        )
        for qid in query_ids:
            try:
                r = self._get(f"{TWITTER_API_BASE}/{qid}/UserByScreenName?{params}")
                if not r.is_success:
                    continue
                data = r.json()
                result = (data.get("data") or {}).get("user", {}).get("result") or {}
                if result.get("__typename") == "UserUnavailable":
                    return None
                user_id = result.get("rest_id")
                uname = (result.get("legacy") or result.get("core") or {}).get("screen_name")
                uname_name = (result.get("legacy") or result.get("core") or {}).get("name")
                if user_id and uname:
                    return User(id=user_id, username=uname, name=uname_name or uname)
            except Exception:
                pass

        # Fallback: REST show.json
        for url in [
            f"https://x.com/i/api/1.1/users/show.json?screen_name={handle}",
            f"https://api.twitter.com/1.1/users/show.json?screen_name={handle}",
        ]:
            try:
                r = self._get(url)
                if not r.is_success:
                    continue
                data = r.json()
                user_id = data.get("id_str") or (str(data["id"]) if data.get("id") else None)
                if user_id:
                    return User(
                        id=user_id,
                        username=data.get("screen_name", handle),
                        name=data.get("name", handle),
                    )
            except Exception:
                pass
        return None

    # ------------------------------------------------------------------
    # Tweet detail / thread / replies
    # ------------------------------------------------------------------

    def _fetch_tweet_detail(self, tweet_id: str, cursor: Optional[str] = None) -> Optional[dict]:
        variables: dict[str, Any] = {
            "focalTweetId": tweet_id,
            "with_rux_injections": False,
            "rankingMode": "Relevance",
            "includePromotedContent": True,
            "withCommunity": True,
            "withQuickPromoteEligibilityTweetFields": True,
            "withBirdwatchNotes": True,
            "withVoice": True,
        }
        if cursor:
            variables["cursor"] = cursor
        features = {
            **tweet_detail_features(),
            "articles_preview_enabled": True,
            "articles_rest_api_enabled": True,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "rweb_video_timestamps_enabled": True,
        }
        field_toggles = {**article_field_toggles(), "withArticleRichContentState": True}
        params = httpx.QueryParams(
            variables=json.dumps(variables),
            features=json.dumps(features),
            fieldToggles=json.dumps(field_toggles),
        )
        query_ids = self._tweet_detail_query_ids()
        had_404 = False
        for qid in query_ids:
            url = f"{TWITTER_API_BASE}/{qid}/TweetDetail?{params}"
            try:
                r = self._get(url)
                if r.status_code == 404:
                    had_404 = True
                    # Try POST fallback
                    body = json.dumps({"variables": variables, "features": features, "queryId": qid})
                    rp = self._post(f"{TWITTER_API_BASE}/{qid}/TweetDetail", body)
                    if rp.status_code == 404:
                        continue
                    r = rp
                if not r.is_success:
                    continue
                data = r.json()
                errors = data.get("errors") or []
                has_data = bool(
                    (data.get("data") or {}).get("tweetResult")
                    or ((data.get("data") or {}).get("threaded_conversation_with_injections_v2") or {}).get("instructions")
                )
                if errors and not has_data:
                    continue
                return data.get("data") or {}
            except Exception:
                pass
        if had_404:
            self._refresh_query_ids()
            for qid in self._tweet_detail_query_ids():
                try:
                    params2 = httpx.QueryParams(
                        variables=json.dumps(variables),
                        features=json.dumps(features),
                        fieldToggles=json.dumps(field_toggles),
                    )
                    r = self._get(f"{TWITTER_API_BASE}/{qid}/TweetDetail?{params2}")
                    if r.is_success:
                        return r.json().get("data") or {}
                except Exception:
                    pass
        return None

    def get_tweet(self, tweet_id: str) -> Optional[Tweet]:
        """Fetch a single tweet by ID."""
        data = self._fetch_tweet_detail(tweet_id)
        if not data:
            return None
        result = (
            (data.get("tweetResult") or {}).get("result")
            or find_tweet_in_instructions(
                (data.get("threaded_conversation_with_injections_v2") or {}).get("instructions"),
                tweet_id,
            )
        )
        mapped = map_tweet_result(result, self._quote_depth)
        if mapped and result and result.get("article"):
            title = _first_text(
                (result["article"].get("article_results") or {}).get("result", {}).get("title"),
                result["article"].get("title"),
            )
            article_text = _extract_article_text(result)
            if title and (not article_text or article_text.strip() == title.strip()):
                user_id = (result.get("core") or {}).get("user_results", {}).get("result", {}).get("rest_id")
                if user_id:
                    fallback = self._fetch_user_article_plain_text(user_id, tweet_id)
                    if fallback.get("plainText"):
                        pt = fallback["plainText"]
                        mapped.text = f"{fallback['title']}\n\n{pt}" if fallback.get("title") else pt
        return mapped

    def get_replies(self, tweet_id: str) -> list[Tweet]:
        """Fetch the first page of replies to a tweet."""
        data = self._fetch_tweet_detail(tweet_id)
        if not data:
            return []
        instructions = (
            (data.get("threaded_conversation_with_injections_v2") or {}).get("instructions")
        )
        tweets = parse_tweets_from_instructions(instructions, self._quote_depth)
        return [t for t in tweets if t.in_reply_to_status_id == tweet_id]

    def get_thread(self, tweet_id: str) -> list[Tweet]:
        """Fetch the full conversation thread for a tweet."""
        data = self._fetch_tweet_detail(tweet_id)
        if not data:
            return []
        instructions = (
            (data.get("threaded_conversation_with_injections_v2") or {}).get("instructions")
        )
        tweets = parse_tweets_from_instructions(instructions, self._quote_depth)
        target = next((t for t in tweets if t.id == tweet_id), None)
        root_id = (target.conversation_id if target else None) or tweet_id
        thread = [t for t in tweets if t.conversation_id == root_id]
        thread.sort(key=lambda t: t.created_at or "")
        return thread

    def _fetch_user_article_plain_text(self, user_id: str, tweet_id: str) -> dict:
        from ._features import _article_features
        variables = {
            "userId": user_id,
            "count": 20,
            "includePromotedContent": True,
            "withVoice": True,
            "withQuickPromoteEligibilityTweetFields": True,
            "withBirdwatchNotes": True,
            "withCommunity": True,
            "withSafetyModeUserFields": True,
        }
        params = httpx.QueryParams(
            variables=json.dumps(variables),
            features=json.dumps(_article_features()),
            fieldToggles=json.dumps(article_field_toggles()),
        )
        qid = self._get_query_id("UserArticlesTweets")
        try:
            r = self._get(f"{TWITTER_API_BASE}/{qid}/UserArticlesTweets?{params}")
            if not r.is_success:
                return {}
            data = r.json()
            instructions = (
                ((data.get("data") or {}).get("user") or {})
                .get("result", {})
                .get("timeline", {})
                .get("timeline", {})
                .get("instructions")
            )
            for instruction in instructions or []:
                for entry in instruction.get("entries") or []:
                    result = (entry.get("content") or {}).get("itemContent", {}).get("tweet_results", {}).get("result")
                    if not result or result.get("rest_id") != tweet_id:
                        continue
                    article_result = (result.get("article") or {}).get("article_results", {}).get("result") or {}
                    return {
                        "title": _first_text(article_result.get("title"), (result.get("article") or {}).get("title")),
                        "plainText": _first_text(article_result.get("plain_text"), (result.get("article") or {}).get("plain_text")),
                    }
        except Exception:
            pass
        return {}

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        count: int = 20,
        *,
        cursor: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> tuple[list[Tweet], Optional[str]]:
        """Search for tweets.  Returns ``(tweets, next_cursor)``."""
        features = search_features()

        def fetch_page(page_cursor, page_count):
            variables: dict[str, Any] = {
                "rawQuery": query,
                "count": page_count,
                "querySource": "typed_query",
                "product": "Latest",
            }
            if page_cursor:
                variables["cursor"] = page_cursor
            params = httpx.QueryParams(variables=json.dumps(variables))
            for qid in self._search_query_ids():
                url = f"{TWITTER_API_BASE}/{qid}/SearchTimeline?{params}"
                try:
                    r = self._post(url, json.dumps({"features": features, "queryId": qid}))
                    if r.status_code == 404:
                        return [], None, True, f"HTTP 404"
                    if not r.is_success:
                        txt = r.text[:200]
                        mismatch = (r.status_code in (400, 422)) and self._is_query_id_mismatch(r.text)
                        return [], None, mismatch, f"HTTP {r.status_code}: {txt}"
                    data = r.json()
                    errors = data.get("errors") or []
                    if errors:
                        mismatch = any(
                            (e or {}).get("extensions", {}).get("code") == "GRAPHQL_VALIDATION_FAILED"
                            for e in errors
                        )
                        return [], None, mismatch, ", ".join(e.get("message", "") for e in errors)
                    instructions = (
                        (data.get("data") or {})
                        .get("search_by_raw_query", {})
                        .get("search_timeline", {})
                        .get("timeline", {})
                        .get("instructions")
                    )
                    page_tweets = parse_tweets_from_instructions(instructions, self._quote_depth)
                    next_cur = extract_cursor_from_instructions(instructions)
                    return page_tweets, next_cur, False, None
                except Exception as exc:
                    return [], None, False, str(exc)
            return [], None, False, "No query IDs available"

        tweets, next_cursor, error = self._paginate(
            fetch_page, count, max_pages=max_pages, initial_cursor=cursor
        )
        return tweets, next_cursor

    def get_mentions(
        self,
        username: Optional[str] = None,
        count: int = 20,
    ) -> tuple[list[Tweet], Optional[str]]:
        """Search for mentions of *username* (defaults to authenticated user)."""
        if username:
            handle = normalize_handle(username)
            if not handle:
                return [], None
            q = f"@{handle}"
        else:
            user = self.get_current_user()
            if not user:
                return [], None
            q = f"@{user.username}"
        return self.search(q, count)

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------

    def tweet(self, text: str) -> Optional[str]:
        """Post a new tweet.  Returns the new tweet ID on success."""
        variables: dict[str, Any] = {
            "tweet_text": text,
            "dark_request": False,
            "media": {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
        }
        return self._create_tweet(variables)

    def reply(self, text: str, reply_to_tweet_id: str) -> Optional[str]:
        """Reply to an existing tweet.  Returns the new tweet ID."""
        variables: dict[str, Any] = {
            "tweet_text": text,
            "reply": {
                "in_reply_to_tweet_id": reply_to_tweet_id,
                "exclude_reply_user_ids": [],
            },
            "dark_request": False,
            "media": {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
        }
        return self._create_tweet(variables)

    def _create_tweet(self, variables: dict) -> Optional[str]:
        self._ensure_client_user_id()
        features = tweet_create_features()
        qid = self._get_query_id("CreateTweet")
        headers = {**self._json_headers(), "referer": "https://x.com/compose/post"}

        def build_body(query_id: str) -> str:
            return json.dumps({"variables": variables, "features": features, "queryId": query_id})

        url = f"{TWITTER_API_BASE}/{qid}/CreateTweet"
        try:
            r = self._http.post(url, headers=headers, content=build_body(qid).encode())
            if r.status_code == 404:
                self._refresh_query_ids()
                qid = self._get_query_id("CreateTweet")
                url = f"{TWITTER_API_BASE}/{qid}/CreateTweet"
                r = self._http.post(url, headers=headers, content=build_body(qid).encode())
                if r.status_code == 404:
                    r = self._http.post(
                        TWITTER_API_BASE, headers=headers, content=build_body(qid).encode()
                    )
            if not r.is_success:
                return None
            data = r.json()
            errors = data.get("errors") or []
            if errors:
                # Fallback to legacy REST on error code 226 (bot detection)
                if any((e or {}).get("code") == 226 for e in errors):
                    return self._post_status_update(variables)
                return None
            return (
                (data.get("data") or {})
                .get("create_tweet", {})
                .get("tweet_results", {})
                .get("result", {})
                .get("rest_id")
            )
        except Exception:
            return None

    def _post_status_update(self, variables: dict) -> Optional[str]:
        """Legacy statuses/update.json fallback for tweet creation."""
        text = variables.get("tweet_text")
        if not isinstance(text, str):
            return None
        data: dict[str, str] = {"status": text}
        reply = variables.get("reply")
        if isinstance(reply, dict) and reply.get("in_reply_to_tweet_id"):
            data["in_reply_to_status_id"] = reply["in_reply_to_tweet_id"]
            data["auto_populate_reply_metadata"] = "true"
        headers = {**self._base_headers(), "referer": "https://x.com/compose/post"}
        try:
            r = self._http.post(TWITTER_STATUS_UPDATE_URL, headers=headers, data=data)
            if not r.is_success:
                return None
            resp_data = r.json()
            return resp_data.get("id_str") or (str(resp_data["id"]) if resp_data.get("id") else None)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Engagement mutations (like, retweet, bookmark)
    # ------------------------------------------------------------------

    def _engagement_mutation(self, operation: str, tweet_id: str) -> bool:
        self._ensure_client_user_id()
        variables = (
            {"tweet_id": tweet_id, "source_tweet_id": tweet_id}
            if operation == "DeleteRetweet"
            else {"tweet_id": tweet_id}
        )
        qid = self._get_query_id(operation)
        headers = {**self._json_headers(), "referer": f"https://x.com/i/status/{tweet_id}"}
        body = json.dumps({"variables": variables, "queryId": qid})
        url = f"{TWITTER_API_BASE}/{qid}/{operation}"
        try:
            r = self._http.post(url, headers=headers, content=body.encode())
            if r.status_code == 404:
                self._refresh_query_ids()
                qid = self._get_query_id(operation)
                body = json.dumps({"variables": variables, "queryId": qid})
                url = f"{TWITTER_API_BASE}/{qid}/{operation}"
                r = self._http.post(url, headers=headers, content=body.encode())
                if r.status_code == 404:
                    r = self._http.post(TWITTER_API_BASE, headers=headers, content=body.encode())
            if not r.is_success:
                return False
            data = r.json()
            return not bool(data.get("errors"))
        except Exception:
            return False

    def like(self, tweet_id: str) -> bool:
        return self._engagement_mutation("FavoriteTweet", tweet_id)

    def unlike(self, tweet_id: str) -> bool:
        return self._engagement_mutation("UnfavoriteTweet", tweet_id)

    def retweet(self, tweet_id: str) -> bool:
        return self._engagement_mutation("CreateRetweet", tweet_id)

    def unretweet(self, tweet_id: str) -> bool:
        return self._engagement_mutation("DeleteRetweet", tweet_id)

    def bookmark(self, tweet_id: str) -> bool:
        return self._engagement_mutation("CreateBookmark", tweet_id)

    def unbookmark(self, tweet_id: str) -> bool:
        qid = self._get_query_id("DeleteBookmark")
        variables = {"tweet_id": tweet_id}
        headers = {**self._json_headers(), "referer": f"https://x.com/i/status/{tweet_id}"}
        body = json.dumps({"variables": variables, "queryId": qid})
        url = f"{TWITTER_API_BASE}/{qid}/DeleteBookmark"
        try:
            r = self._http.post(url, headers=headers, content=body.encode())
            if r.status_code == 404:
                self._refresh_query_ids()
                qid = self._get_query_id("DeleteBookmark")
                body = json.dumps({"variables": variables, "queryId": qid})
                url = f"{TWITTER_API_BASE}/{qid}/DeleteBookmark"
                r = self._http.post(url, headers=headers, content=body.encode())
                if r.status_code == 404:
                    r = self._http.post(TWITTER_API_BASE, headers=headers, content=body.encode())
            if not r.is_success:
                return False
            return not bool(r.json().get("errors"))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Follow / unfollow
    # ------------------------------------------------------------------

    def follow(self, user_id: str) -> bool:
        self._ensure_client_user_id()
        return self._friendship_rest(user_id, "create") or self._friendship_graphql(user_id, follow=True)

    def unfollow(self, user_id: str) -> bool:
        self._ensure_client_user_id()
        return self._friendship_rest(user_id, "destroy") or self._friendship_graphql(user_id, follow=False)

    def _friendship_rest(self, user_id: str, action: str) -> bool:
        for url in [
            f"https://x.com/i/api/1.1/friendships/{action}.json",
            f"https://api.twitter.com/1.1/friendships/{action}.json",
        ]:
            try:
                r = self._post_form(url, {"user_id": user_id, "skip_status": "true"})
                if r.is_success:
                    return True
                # Code 160 = already following/unfollowing — treat as success
                errors = (r.json().get("errors") or [])
                if any((e or {}).get("code") == 160 for e in errors):
                    return True
            except Exception:
                pass
        return False

    def _friendship_graphql(self, user_id: str, follow: bool) -> bool:
        operation = "CreateFriendship" if follow else "DestroyFriendship"
        fallbacks = (
            ["8h9JVdV8dlSyqyRDJEPCsA", "OPwKc1HXnBT_bWXfAlo-9g"]
            if follow
            else ["ppXWuagMNXgvzx6WoXBW0Q", "8h9JVdV8dlSyqyRDJEPCsA"]
        )
        qids = list(dict.fromkeys([self._get_query_id(operation)] + fallbacks))
        variables = {"user_id": user_id}
        had_404 = False
        for qid in qids:
            try:
                body = json.dumps({"variables": variables, "queryId": qid})
                r = self._http.post(
                    f"{TWITTER_API_BASE}/{qid}/{operation}",
                    headers=self._json_headers(),
                    content=body.encode(),
                )
                if r.status_code == 404:
                    had_404 = True
                    continue
                if not r.is_success:
                    continue
                data = r.json()
                if data.get("errors"):
                    continue
                return True
            except Exception:
                pass
        if had_404:
            self._refresh_query_ids()
            qid2 = self._get_query_id(operation)
            try:
                body = json.dumps({"variables": variables, "queryId": qid2})
                r = self._http.post(
                    f"{TWITTER_API_BASE}/{qid2}/{operation}",
                    headers=self._json_headers(),
                    content=body.encode(),
                )
                return r.is_success and not r.json().get("errors")
            except Exception:
                pass
        return False

    # ------------------------------------------------------------------
    # Bookmarks
    # ------------------------------------------------------------------

    def get_bookmarks(
        self,
        count: int = 20,
        *,
        folder_id: Optional[str] = None,
        cursor: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> tuple[list[Tweet], Optional[str]]:
        """Fetch bookmarked tweets.  Returns ``(tweets, next_cursor)``."""
        if folder_id:
            return self._bookmarks_folder(folder_id, count, cursor, max_pages)
        return self._bookmarks_main(count, cursor, max_pages)

    def _bookmarks_main(
        self,
        limit: int,
        initial_cursor: Optional[str],
        max_pages: Optional[int],
    ) -> tuple[list[Tweet], Optional[str]]:
        features = bookmarks_features()
        qids = list(dict.fromkeys([
            self._get_query_id("Bookmarks"),
            "RV1g3b8n_SGOHwkqKYSCFw",
            "tmd4ifV8RHltzn8ymGg1aw",
        ]))

        def fetch_page(page_cursor, page_count):
            variables: dict[str, Any] = {
                "count": page_count,
                "includePromotedContent": False,
                "withDownvotePerspective": False,
                "withReactionsMetadata": False,
                "withReactionsPerspective": False,
            }
            if page_cursor:
                variables["cursor"] = page_cursor
            params = httpx.QueryParams(
                variables=json.dumps(variables),
                features=json.dumps(features),
            )
            for qid in qids:
                url = f"{TWITTER_API_BASE}/{qid}/Bookmarks?{params}"
                try:
                    r = self._get_with_retry(url)
                    if r.status_code == 404:
                        return [], None, True, "HTTP 404"
                    if not r.is_success:
                        return [], None, False, f"HTTP {r.status_code}: {r.text[:200]}"
                    data = r.json()
                    instructions = (
                        (data.get("data") or {})
                        .get("bookmark_timeline_v2", {})
                        .get("timeline", {})
                        .get("instructions")
                    )
                    page_tweets = parse_tweets_from_instructions(instructions, self._quote_depth)
                    next_cur = extract_cursor_from_instructions(instructions)
                    return page_tweets, next_cur, False, None
                except Exception as exc:
                    return [], None, False, str(exc)
            return [], None, False, "No query IDs available"

        tweets, next_cursor, error = self._paginate(fetch_page, limit, max_pages, initial_cursor)
        return tweets, next_cursor

    def _bookmarks_folder(
        self,
        folder_id: str,
        limit: int,
        initial_cursor: Optional[str],
        max_pages: Optional[int],
    ) -> tuple[list[Tweet], Optional[str]]:
        features = bookmarks_features()
        qids = list(dict.fromkeys([
            self._get_query_id("BookmarkFolderTimeline"),
            "KJIQpsvxrTfRIlbaRIySHQ",
        ]))

        def fetch_page(page_cursor, page_count):
            variables: dict[str, Any] = {
                "bookmark_collection_id": folder_id,
                "includePromotedContent": True,
                "count": page_count,
            }
            if page_cursor:
                variables["cursor"] = page_cursor
            params = httpx.QueryParams(
                variables=json.dumps(variables),
                features=json.dumps(features),
            )
            for qid in qids:
                url = f"{TWITTER_API_BASE}/{qid}/BookmarkFolderTimeline?{params}"
                try:
                    r = self._get_with_retry(url)
                    if r.status_code == 404:
                        return [], None, True, "HTTP 404"
                    if not r.is_success:
                        return [], None, False, f"HTTP {r.status_code}: {r.text[:200]}"
                    data = r.json()
                    instructions = (
                        (data.get("data") or {})
                        .get("bookmark_collection_timeline", {})
                        .get("timeline", {})
                        .get("instructions")
                    )
                    page_tweets = parse_tweets_from_instructions(instructions, self._quote_depth)
                    next_cur = extract_cursor_from_instructions(instructions)
                    return page_tweets, next_cur, False, None
                except Exception as exc:
                    return [], None, False, str(exc)
            return [], None, False, "No query IDs available"

        tweets, next_cursor, error = self._paginate(fetch_page, limit, max_pages, initial_cursor)
        return tweets, next_cursor

    # ------------------------------------------------------------------
    # Likes
    # ------------------------------------------------------------------

    def get_likes(
        self,
        count: int = 20,
        *,
        cursor: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> tuple[list[Tweet], Optional[str]]:
        """Fetch liked tweets for the current user.  Returns ``(tweets, next_cursor)``."""
        user = self.get_current_user()
        if not user:
            return [], None
        features = likes_features()
        qids = list(dict.fromkeys([self._get_query_id("Likes"), "JR2gceKucIKcVNB_9JkhsA"]))

        def fetch_page(page_cursor, page_count):
            variables: dict[str, Any] = {
                "userId": user.id,
                "count": page_count,
                "includePromotedContent": False,
                "withClientEventToken": False,
                "withBirdwatchNotes": False,
                "withVoice": True,
            }
            if page_cursor:
                variables["cursor"] = page_cursor
            params = httpx.QueryParams(
                variables=json.dumps(variables),
                features=json.dumps(features),
            )
            for qid in qids:
                url = f"{TWITTER_API_BASE}/{qid}/Likes?{params}"
                try:
                    r = self._get(url)
                    if r.status_code == 404:
                        return [], None, True, "HTTP 404"
                    if not r.is_success:
                        return [], None, False, f"HTTP {r.status_code}: {r.text[:200]}"
                    data = r.json()
                    instructions = (
                        (data.get("data") or {})
                        .get("user", {})
                        .get("result", {})
                        .get("timeline", {})
                        .get("timeline", {})
                        .get("instructions")
                    )
                    page_tweets = parse_tweets_from_instructions(instructions, self._quote_depth)
                    next_cur = extract_cursor_from_instructions(instructions)
                    return page_tweets, next_cur, False, None
                except Exception as exc:
                    return [], None, False, str(exc)
            return [], None, False, "No query IDs available"

        tweets, next_cursor, _ = self._paginate(fetch_page, count, max_pages, cursor)
        return tweets, next_cursor

    # ------------------------------------------------------------------
    # User tweets
    # ------------------------------------------------------------------

    def get_user_tweets(
        self,
        user_id: str,
        count: int = 20,
        *,
        cursor: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> tuple[list[Tweet], Optional[str]]:
        """Fetch tweets from a user's profile timeline.  Returns ``(tweets, next_cursor)``."""
        features = user_tweets_features()
        qids = list(dict.fromkeys([self._get_query_id("UserTweets"), "Wms1GvIiHXAPBaCr9KblaA"]))
        hard_max = 10
        computed_max = max(1, math.ceil(count / _PAGE_SIZE))
        effective_max = min(hard_max, max_pages or computed_max)

        def fetch_page(page_cursor, page_count):
            variables: dict[str, Any] = {
                "userId": user_id,
                "count": page_count,
                "includePromotedContent": False,
                "withQuickPromoteEligibilityTweetFields": True,
                "withVoice": True,
            }
            if page_cursor:
                variables["cursor"] = page_cursor
            params = httpx.QueryParams(
                variables=json.dumps(variables),
                features=json.dumps(features),
                fieldToggles=json.dumps({"withArticlePlainText": False}),
            )
            for qid in qids:
                url = f"{TWITTER_API_BASE}/{qid}/UserTweets?{params}"
                try:
                    r = self._get(url)
                    if r.status_code == 404:
                        return [], None, True, "HTTP 404"
                    if not r.is_success:
                        return [], None, False, f"HTTP {r.status_code}: {r.text[:200]}"
                    data = r.json()
                    errors = data.get("errors") or []
                    instructions = (
                        (data.get("data") or {})
                        .get("user", {})
                        .get("result", {})
                        .get("timeline", {})
                        .get("timeline", {})
                        .get("instructions")
                    )
                    if errors:
                        msgs = ", ".join(e.get("message", "") for e in errors)
                        if not instructions:
                            return [], None, False, msgs
                    page_tweets = parse_tweets_from_instructions(instructions, self._quote_depth)
                    next_cur = extract_cursor_from_instructions(instructions)
                    return page_tweets, next_cur, False, None
                except Exception as exc:
                    return [], None, False, str(exc)
            return [], None, False, "No query IDs available"

        tweets, next_cursor, _ = self._paginate(fetch_page, count, effective_max, cursor)
        return tweets, next_cursor

    # ------------------------------------------------------------------
    # Home timeline
    # ------------------------------------------------------------------

    def get_home_timeline(self, count: int = 20) -> list[Tweet]:
        """Fetch the authenticated user's 'For You' home timeline."""
        return self._home_timeline("HomeTimeline", count)

    def get_home_latest_timeline(self, count: int = 20) -> list[Tweet]:
        """Fetch the authenticated user's 'Following' (chronological) timeline."""
        return self._home_timeline("HomeLatestTimeline", count)

    def _home_timeline(self, operation: str, count: int) -> list[Tweet]:
        features = home_timeline_features()
        if operation == "HomeTimeline":
            qids = list(dict.fromkeys([self._get_query_id("HomeTimeline"), "edseUwk9sP5Phz__9TIRnA"]))
        else:
            qids = list(dict.fromkeys([self._get_query_id("HomeLatestTimeline"), "iOEZpOdfekFsxSlPQCQtPg"]))

        seen: set[str] = set()
        tweets: list[Tweet] = []
        cursor: Optional[str] = None

        while len(tweets) < count:
            page_count = min(_PAGE_SIZE, count - len(tweets))
            had_404 = False
            success = False
            for qid in qids:
                variables: dict[str, Any] = {
                    "count": page_count,
                    "includePromotedContent": True,
                    "latestControlAvailable": True,
                    "requestContext": "launch",
                    "withCommunity": True,
                }
                if cursor:
                    variables["cursor"] = cursor
                params = httpx.QueryParams(
                    variables=json.dumps(variables),
                    features=json.dumps(features),
                )
                url = f"{TWITTER_API_BASE}/{qid}/{operation}?{params}"
                try:
                    r = self._get(url)
                    if r.status_code == 404:
                        had_404 = True
                        continue
                    if not r.is_success:
                        break
                    data = r.json()
                    if data.get("errors"):
                        break
                    instructions = (
                        (data.get("data") or {})
                        .get("home", {})
                        .get("home_timeline_urt", {})
                        .get("instructions")
                    )
                    page_tweets = parse_tweets_from_instructions(instructions, self._quote_depth)
                    new_cursor = extract_cursor_from_instructions(instructions)
                    added = 0
                    for t in page_tweets:
                        if t.id not in seen:
                            seen.add(t.id)
                            tweets.append(t)
                            added += 1
                    if not new_cursor or new_cursor == cursor or not page_tweets or added == 0:
                        return tweets
                    cursor = new_cursor
                    success = True
                    break
                except Exception:
                    pass
            if not success:
                if had_404:
                    self._refresh_query_ids()
                    if operation == "HomeTimeline":
                        qids = [self._get_query_id("HomeTimeline")]
                    else:
                        qids = [self._get_query_id("HomeLatestTimeline")]
                else:
                    break
        return tweets

    # ------------------------------------------------------------------
    # Following / Followers
    # ------------------------------------------------------------------

    def get_following(
        self,
        user_id: str,
        count: int = 20,
        *,
        cursor: Optional[str] = None,
    ) -> tuple[list[User], Optional[str]]:
        """Return users that *user_id* follows.  Returns ``(users, next_cursor)``."""
        return self._follow_list("Following", user_id, count, cursor)

    def get_followers(
        self,
        user_id: str,
        count: int = 20,
        *,
        cursor: Optional[str] = None,
    ) -> tuple[list[User], Optional[str]]:
        """Return users that follow *user_id*.  Returns ``(users, next_cursor)``."""
        return self._follow_list("Followers", user_id, count, cursor)

    def _follow_list(
        self,
        operation: str,
        user_id: str,
        count: int,
        cursor: Optional[str],
    ) -> tuple[list[User], Optional[str]]:
        fallback_ids = {
            "Following": "BEkNpEt5pNETESoqMsTEGA",
            "Followers": "kuFUYP9eV1FPoEy4N-pi7w",
        }
        qids = list(dict.fromkeys([self._get_query_id(operation), fallback_ids[operation]]))
        features = following_features()
        variables: dict[str, Any] = {
            "userId": user_id,
            "count": count,
            "includePromotedContent": False,
        }
        if cursor:
            variables["cursor"] = cursor
        params = httpx.QueryParams(
            variables=json.dumps(variables),
            features=json.dumps(features),
        )
        had_404 = False
        for qid in qids:
            url = f"{TWITTER_API_BASE}/{qid}/{operation}?{params}"
            try:
                r = self._get(url)
                if r.status_code == 404:
                    had_404 = True
                    continue
                if not r.is_success:
                    continue
                data = r.json()
                if data.get("errors"):
                    continue
                instructions = (
                    (data.get("data") or {})
                    .get("user", {})
                    .get("result", {})
                    .get("timeline", {})
                    .get("timeline", {})
                    .get("instructions")
                )
                users = parse_users_from_instructions(instructions)
                next_cursor = extract_cursor_from_instructions(instructions)
                return users, next_cursor
            except Exception:
                pass

        if had_404:
            self._refresh_query_ids()
            # REST fallback
            rest_op = "friends" if operation == "Following" else "followers"
            for url in [
                f"https://x.com/i/api/1.1/{rest_op}/list.json",
                f"https://api.twitter.com/1.1/{rest_op}/list.json",
            ]:
                params_rest = {"user_id": user_id, "count": str(count), "skip_status": "true"}
                if cursor:
                    params_rest["cursor"] = cursor
                try:
                    r = self._http.get(url, headers=self._json_headers(), params=params_rest)
                    if not r.is_success:
                        continue
                    data = r.json()
                    raw_users = data.get("users") or []
                    users = [
                        User(
                            id=u.get("id_str") or str(u["id"]),
                            username=u["screen_name"],
                            name=u.get("name", u["screen_name"]),
                            description=u.get("description"),
                            followers_count=u.get("followers_count"),
                            following_count=u.get("friends_count"),
                            is_blue_verified=u.get("verified"),
                            profile_image_url=u.get("profile_image_url_https"),
                            created_at=u.get("created_at"),
                        )
                        for u in raw_users
                        if u.get("screen_name")
                    ]
                    nc = data.get("next_cursor_str")
                    return users, (nc if nc and nc != "0" else None)
                except Exception:
                    pass
        return [], None

    # ------------------------------------------------------------------
    # Lists
    # ------------------------------------------------------------------

    def get_owned_lists(self, count: int = 100) -> list[TwitterList]:
        user = self.get_current_user()
        if not user:
            return []
        return self._fetch_lists("ListOwnerships", user.id, count)

    def get_list_memberships(self, count: int = 100) -> list[TwitterList]:
        user = self.get_current_user()
        if not user:
            return []
        return self._fetch_lists("ListMemberships", user.id, count)

    def _fetch_lists(self, operation: str, user_id: str, count: int) -> list[TwitterList]:
        fallback = {"ListOwnerships": "wQcOSjSQ8NtgxIwvYl1lMg", "ListMemberships": "BlEXXdARdSeL_0KyKHHvvg"}
        qids = list(dict.fromkeys([self._get_query_id(operation), fallback[operation]]))
        features = lists_features()
        variables = {
            "userId": user_id,
            "count": count,
            "isListMembershipShown": True,
            "isListMemberTargetUserId": user_id,
        }
        params = httpx.QueryParams(
            variables=json.dumps(variables),
            features=json.dumps(features),
        )
        had_404 = False
        for qid in qids:
            url = f"{TWITTER_API_BASE}/{qid}/{operation}?{params}"
            try:
                r = self._get(url)
                if r.status_code == 404:
                    had_404 = True
                    continue
                if not r.is_success:
                    continue
                data = r.json()
                if data.get("errors"):
                    continue
                instructions = (
                    (data.get("data") or {})
                    .get("user", {})
                    .get("result", {})
                    .get("timeline", {})
                    .get("timeline", {})
                    .get("instructions")
                )
                return self._parse_lists_from_instructions(instructions)
            except Exception:
                pass
        if had_404:
            self._refresh_query_ids()
        return []

    @staticmethod
    def _parse_lists_from_instructions(instructions: Optional[list]) -> list[TwitterList]:
        result: list[TwitterList] = []
        for instruction in instructions or []:
            for entry in instruction.get("entries") or []:
                lr = (entry.get("content") or {}).get("itemContent", {}).get("list")
                if not lr or not lr.get("id_str") or not lr.get("name"):
                    continue
                owner_result = (lr.get("user_results") or {}).get("result")
                owner = None
                if owner_result:
                    owner_legacy = owner_result.get("legacy") or {}
                    owner = Author(
                        username=owner_legacy.get("screen_name", ""),
                        name=owner_legacy.get("name", ""),
                    )
                result.append(
                    TwitterList(
                        id=lr["id_str"],
                        name=lr["name"],
                        description=lr.get("description"),
                        member_count=lr.get("member_count"),
                        subscriber_count=lr.get("subscriber_count"),
                        is_private=(lr.get("mode") or "").lower() == "private",
                        created_at=lr.get("created_at"),
                        owner=owner,
                    )
                )
        return result

    def get_list_timeline(
        self,
        list_id: str,
        count: int = 20,
        *,
        cursor: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> tuple[list[Tweet], Optional[str]]:
        """Fetch tweets from a list timeline.  Returns ``(tweets, next_cursor)``."""
        features = lists_features()
        qids = list(dict.fromkeys([self._get_query_id("ListLatestTweetsTimeline"), "2TemLyqrMpTeAmysdbnVqw"]))

        def fetch_page(page_cursor, page_count):
            variables: dict[str, Any] = {"listId": list_id, "count": page_count}
            if page_cursor:
                variables["cursor"] = page_cursor
            params = httpx.QueryParams(
                variables=json.dumps(variables),
                features=json.dumps(features),
            )
            for qid in qids:
                url = f"{TWITTER_API_BASE}/{qid}/ListLatestTweetsTimeline?{params}"
                try:
                    r = self._get(url)
                    if r.status_code == 404:
                        return [], None, True, "HTTP 404"
                    if not r.is_success:
                        return [], None, False, f"HTTP {r.status_code}"
                    data = r.json()
                    if data.get("errors"):
                        continue
                    instructions = (
                        (data.get("data") or {})
                        .get("list", {})
                        .get("tweets_timeline", {})
                        .get("timeline", {})
                        .get("instructions")
                    )
                    page_tweets = parse_tweets_from_instructions(instructions, self._quote_depth)
                    next_cur = extract_cursor_from_instructions(instructions)
                    return page_tweets, next_cur, False, None
                except Exception as exc:
                    return [], None, False, str(exc)
            return [], None, False, "No query IDs available"

        tweets, next_cursor, _ = self._paginate(fetch_page, count, max_pages, cursor)
        return tweets, next_cursor

    # ------------------------------------------------------------------
    # About account
    # ------------------------------------------------------------------

    def get_user_about_account(self, username: str) -> Optional[AboutProfile]:
        """Fetch 'About this account' data for a user."""
        handle = normalize_handle(username)
        if not handle:
            return None
        qids = list(dict.fromkeys([self._get_query_id("AboutAccountQuery"), "zs_jFPFT78rBpXv9Z3U2YQ"]))
        params = httpx.QueryParams(variables=json.dumps({"screenName": handle}))
        had_404 = False
        for qid in qids:
            try:
                r = self._get(f"{TWITTER_API_BASE}/{qid}/AboutAccountQuery?{params}")
                if r.status_code == 404:
                    had_404 = True
                    continue
                if not r.is_success:
                    continue
                data = r.json()
                if data.get("errors"):
                    continue
                about = (
                    (data.get("data") or {})
                    .get("user_result_by_screen_name", {})
                    .get("result", {})
                    .get("about_profile")
                )
                if not about:
                    continue
                return AboutProfile(
                    account_based_in=about.get("account_based_in"),
                    source=about.get("source"),
                    created_country_accurate=about.get("created_country_accurate"),
                    location_accurate=about.get("location_accurate"),
                    learn_more_url=about.get("learn_more_url"),
                )
            except Exception:
                pass
        if had_404:
            self._refresh_query_ids()
        return None

    # ------------------------------------------------------------------
    # News / trending
    # ------------------------------------------------------------------

    def get_news(
        self,
        count: int = 10,
        *,
        ai_only: bool = False,
        with_tweets: bool = False,
        tweets_per_item: int = 5,
        tabs: Optional[list[str]] = None,
    ) -> list[NewsItem]:
        """Fetch news and trending topics from X's Explore tabs."""
        if tabs is None:
            tabs = ["forYou", "news", "sports", "entertainment"]
        features = explore_features()
        qid = self._get_query_id("GenericTimelineById")
        all_items: list[NewsItem] = []
        seen_headlines: set[str] = set()

        for tab in tabs:
            timeline_id = _TIMELINE_IDS.get(tab)
            if not timeline_id:
                continue
            try:
                variables = {
                    "timelineId": timeline_id,
                    "count": count * 2,
                    "includePromotedContent": False,
                }
                params = httpx.QueryParams(
                    variables=json.dumps(variables),
                    features=json.dumps(features),
                )
                r = self._get(f"{TWITTER_API_BASE}/{qid}/GenericTimelineById?{params}")
                if not r.is_success:
                    continue
                data = r.json()
                if data.get("errors"):
                    continue
                tab_items = self._parse_timeline_tab_items(data, tab, count, ai_only)
                for item in tab_items:
                    if item.headline not in seen_headlines:
                        seen_headlines.add(item.headline)
                        all_items.append(item)
                if len(all_items) >= count:
                    break
            except Exception:
                pass

        items = all_items[:count]
        if with_tweets:
            for item in items:
                try:
                    page_tweets, _ = self.search(item.headline, tweets_per_item)
                    item.tweets = page_tweets
                except Exception:
                    pass
        return items

    def _parse_timeline_tab_items(
        self, data: dict, source: str, max_count: int, ai_only: bool
    ) -> list[NewsItem]:
        items: list[NewsItem] = []
        seen: set[str] = set()
        timeline = (data.get("data") or {}).get("timeline", {}).get("timeline") or {}
        instructions = timeline.get("instructions") or []
        for instruction in instructions:
            entries = instruction.get("entries") or (
                [instruction["entry"]] if instruction.get("entry") else []
            )
            for entry in entries:
                if len(items) >= max_count:
                    break
                content = entry.get("content") or {}
                if content.get("itemContent"):
                    item = self._parse_news_item(content["itemContent"], entry.get("entryId", ""), source, seen, ai_only)
                    if item:
                        items.append(item)
                for sub in content.get("items") or []:
                    item_content = sub.get("itemContent") or (sub.get("item") or {}).get("itemContent")
                    if item_content:
                        item = self._parse_news_item(item_content, entry.get("entryId", ""), source, seen, ai_only)
                        if item:
                            items.append(item)
                        if len(items) >= max_count:
                            break
        return items

    @staticmethod
    def _parse_news_item(
        item_content: dict,
        entry_id: str,
        source: str,
        seen: set[str],
        ai_only: bool,
    ) -> Optional[NewsItem]:
        headline = item_content.get("name") or item_content.get("title")
        if not headline or headline in seen:
            return None

        trend_metadata = item_content.get("trend_metadata") or {}
        trend_url = (item_content.get("trend_url") or {}).get("url") or trend_metadata.get("url", {}).get("url")
        social_context_text = (item_content.get("social_context") or {}).get("text") or ""
        is_full_sentence = len(headline.split()) >= 5
        has_news_category = "News" in social_context_text or "hours ago" in social_context_text
        is_ai_trend = item_content.get("is_ai_trend") is True
        is_ai = is_ai_trend or (is_full_sentence and has_news_category)

        if ai_only and not is_ai:
            return None

        seen.add(headline)
        post_count: Optional[int] = None
        time_ago: Optional[str] = None
        category = "Trending"

        if social_context_text:
            for part in [p.strip() for p in social_context_text.split("·")]:
                if "ago" in part:
                    time_ago = part
                elif _POST_COUNT_RE.search(part):
                    post_count = _parse_post_count(part)
                else:
                    category = part

        if trend_metadata.get("meta_description"):
            pc = _parse_post_count(trend_metadata["meta_description"])
            if pc:
                post_count = pc

        domain_ctx = trend_metadata.get("domain_context")
        if domain_ctx and category in ("Trending", "News"):
            category = domain_ctx

        item_id = trend_url or f"{entry_id}-{headline}" or f"{source}-{headline}"
        return NewsItem(
            id=item_id,
            headline=headline,
            category=f"AI · {category}" if is_ai else category,
            time_ago=time_ago,
            post_count=post_count,
            description=item_content.get("description"),
            url=trend_url,
        )

    # ------------------------------------------------------------------
    # Query ID cache management
    # ------------------------------------------------------------------

    def refresh_query_ids(self) -> dict:
        """Force-refresh the GraphQL query ID cache and return info."""
        self._refresh_query_ids()
        return query_id_store.info()

    def query_ids_info(self) -> dict:
        """Return current query ID cache state."""
        return query_id_store.info()
