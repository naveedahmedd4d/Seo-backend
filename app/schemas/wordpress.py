from pydantic import BaseModel, HttpUrl
from typing import Optional


class WordPressSiteCreate(BaseModel):
    name: str
    url: str
    username: str
    api_key: str


class WordPressSiteUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    username: Optional[str] = None
    api_key: Optional[str] = None


class WordPressSiteResponse(BaseModel):
    id: int
    name: str
    url: str
    username: str
    created_at: str

    class Config:
        from_attributes = True
    
    @classmethod
    def from_orm(cls, obj):
        """Convert ORM object to response model"""
        data = {
            "id": obj.id,
            "name": obj.name,
            "url": obj.url,
            "username": obj.username,
            "created_at": obj.created_at.isoformat() if obj.created_at else ""
        }
        return cls(**data)


class WordPressSiteTest(BaseModel):
    url: str
    username: str
    api_key: str


class WordPressTestResponse(BaseModel):
    success: bool
    message: str
    user: Optional[str] = None


class WordPressPublishRequest(BaseModel):
    site_id: int
    title: str
    content: str
    status: str = "draft"


class WordPressPublishResponse(BaseModel):
    success: bool
    message: str
    post_id: Optional[int] = None
    link: Optional[str] = None

