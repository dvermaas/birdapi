from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Author:
    username: str
    name: str


@dataclass
class MediaItem:
    type: str  # "photo", "video", "animated_gif"
    url: str
    width: Optional[int] = None
    height: Optional[int] = None
    preview_url: Optional[str] = None
    video_url: Optional[str] = None
    duration_ms: Optional[int] = None


@dataclass
class ArticleMetadata:
    title: str
    preview_text: Optional[str] = None


@dataclass
class Tweet:
    id: str
    text: str
    author: Author
    created_at: Optional[str] = None
    reply_count: Optional[int] = None
    retweet_count: Optional[int] = None
    like_count: Optional[int] = None
    conversation_id: Optional[str] = None
    in_reply_to_status_id: Optional[str] = None
    author_id: Optional[str] = None
    quoted_tweet: Optional[Tweet] = None
    media: Optional[list[MediaItem]] = None
    article: Optional[ArticleMetadata] = None
    _raw: Optional[Any] = field(default=None, repr=False)


@dataclass
class User:
    id: str
    username: str
    name: str
    description: Optional[str] = None
    followers_count: Optional[int] = None
    following_count: Optional[int] = None
    is_blue_verified: Optional[bool] = None
    profile_image_url: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class TwitterList:
    id: str
    name: str
    description: Optional[str] = None
    member_count: Optional[int] = None
    subscriber_count: Optional[int] = None
    is_private: bool = False
    created_at: Optional[str] = None
    owner: Optional[Author] = None


@dataclass
class AboutProfile:
    account_based_in: Optional[str] = None
    source: Optional[str] = None
    created_country_accurate: Optional[str] = None
    location_accurate: Optional[str] = None
    learn_more_url: Optional[str] = None


@dataclass
class NewsItem:
    id: str
    headline: str
    category: Optional[str] = None
    time_ago: Optional[str] = None
    post_count: Optional[int] = None
    description: Optional[str] = None
    url: Optional[str] = None
    tweets: Optional[list[Tweet]] = None
    _raw: Optional[Any] = field(default=None, repr=False)
