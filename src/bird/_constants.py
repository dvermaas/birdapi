import re

TWITTER_API_BASE = "https://x.com/i/api/graphql"
TWITTER_GRAPHQL_POST_URL = "https://x.com/i/api/graphql"
TWITTER_STATUS_UPDATE_URL = "https://x.com/i/api/1.1/statuses/update.json"

# Public bearer token used by the X/Twitter web client
BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# Fallback query IDs — refreshed at runtime from x.com bundles; these keep
# the client usable if the cache is missing or stale.
FALLBACK_QUERY_IDS: dict[str, str] = {
    "CreateTweet": "TAJw1rBsjAtdNgTdlo2oeg",
    "CreateRetweet": "ojPdsZsimiJrUGLR1sjUtA",
    "DeleteRetweet": "iQtK4dl5hBmXewYZuEOKVw",
    "CreateFriendship": "8h9JVdV8dlSyqyRDJEPCsA",
    "DestroyFriendship": "ppXWuagMNXgvzx6WoXBW0Q",
    "FavoriteTweet": "lI07N6Otwv1PhnEgXILM7A",
    "UnfavoriteTweet": "ZYKSe-w7KEslx3JhSIk5LA",
    "CreateBookmark": "aoDbu3RHznuiSkQ9aNM67Q",
    "DeleteBookmark": "Wlmlj2-xzyS1GN3a6cj-mQ",
    "TweetDetail": "97JF30KziU00483E_8elBA",
    "SearchTimeline": "M1jEez78PEfVfbQLvlWMvQ",
    "UserArticlesTweets": "8zBy9h4L90aDL02RsBcCFg",
    "UserTweets": "Wms1GvIiHXAPBaCr9KblaA",
    "Bookmarks": "RV1g3b8n_SGOHwkqKYSCFw",
    "Following": "BEkNpEt5pNETESoqMsTEGA",
    "Followers": "kuFUYP9eV1FPoEy4N-pi7w",
    "Likes": "JR2gceKucIKcVNB_9JkhsA",
    "BookmarkFolderTimeline": "KJIQpsvxrTfRIlbaRIySHQ",
    "ListOwnerships": "wQcOSjSQ8NtgxIwvYl1lMg",
    "ListMemberships": "BlEXXdARdSeL_0KyKHHvvg",
    "ListLatestTweetsTimeline": "2TemLyqrMpTeAmysdbnVqw",
    "HomeTimeline": "edseUwk9sP5Phz__9TIRnA",
    "HomeLatestTimeline": "iOEZpOdfekFsxSlPQCQtPg",
    "ExploreSidebar": "lpSN4M6qpimkF4nRFPE3nQ",
    "ExplorePage": "kheAINB_4pzRDqkzG3K-ng",
    "GenericTimelineById": "uGSr7alSjR9v6QJAIaqSKQ",
    "TrendHistory": "Sj4T-jSB9pr0Mxtsc1UKZQ",
    "AboutAccountQuery": "zs_jFPFT78rBpXv9Z3U2YQ",
}

SETTINGS_SCREEN_NAME_RE = re.compile(r'"screen_name":"([^"]+)"')
SETTINGS_USER_ID_RE = re.compile(r'"user_id"\s*:\s*"(\d+)"')
SETTINGS_NAME_RE = re.compile(r'"name":"([^"\\]*(?:\\.[^"\\]*)*)"')
