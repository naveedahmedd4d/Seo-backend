from typing import Any, List, Optional

from pydantic import BaseModel


class SocialAccountResponse(BaseModel):
    id: int
    platform: str
    account_label: Optional[str] = None
    connected: bool = True
    metadata: dict[str, Any] = {}
    created_at: str
    updated_at: Optional[str] = None


class SocialAccountConnectResponse(BaseModel):
    authorization_url: str


class PinterestBoardItem(BaseModel):
    id: str
    name: str


class PinterestBoardsResponse(BaseModel):
    boards: List[PinterestBoardItem]
    default_board_id: Optional[str] = None


class PinterestBoardUpdateRequest(BaseModel):
    board_id: str


class PinterestSetupResponse(BaseModel):
    redirect_uri: str
    has_app_credentials: bool
    client_id: Optional[str] = None
    developer_portal_url: str = "https://developers.pinterest.com/apps/"


class PinterestAppCredentialsRequest(BaseModel):
    client_id: str
    client_secret: str


class PinterestConnectRequest(BaseModel):
    client_id: str
    client_secret: str


class OAuthSetupResponse(BaseModel):
    redirect_uri: str
    has_app_credentials: bool
    client_id: Optional[str] = None
    developer_portal_url: str


class OAuthConnectRequest(BaseModel):
    client_id: str
    client_secret: str


class MastodonSetupResponse(OAuthSetupResponse):
    instance_url: Optional[str] = None
    developer_portal_url: str = "https://docs.joinmastodon.org/client/intro/"


class MastodonConnectRequest(OAuthConnectRequest):
    instance_url: str


class BlueskyConnectRequest(BaseModel):
    identifier: str
    app_password: str


class BlueskyConnectResponse(BaseModel):
    connected: bool = True
    account_label: Optional[str] = None


class ApiKeyConnectRequest(BaseModel):
    api_key: str


class ApiKeyConnectResponse(BaseModel):
    connected: bool = True
    account_label: Optional[str] = None


class BloggerBlogItem(BaseModel):
    id: str
    name: str


class BloggerBlogsResponse(BaseModel):
    blogs: List[BloggerBlogItem]
    default_blog_id: Optional[str] = None


class BloggerBlogUpdateRequest(BaseModel):
    blog_id: str


class RedditSubredditItem(BaseModel):
    name: str
    title: str


class RedditSubredditsResponse(BaseModel):
    subreddits: List[RedditSubredditItem]
    default_subreddit: Optional[str] = None


class RedditSubredditUpdateRequest(BaseModel):
    subreddit: str
