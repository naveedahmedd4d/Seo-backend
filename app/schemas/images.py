from pydantic import BaseModel
from typing import List, Optional, Any


class ImageSearchRequest(BaseModel):
    keyword: str
    num_results: int = 10


class ImageResult(BaseModel):
    title: str
    link: str
    original: str
    thumbnail: str
    source: str


class ImageSearchResponse(BaseModel):
    keyword: str
    images: List[ImageResult]
    count: int


class ImageSearchHistoryResponse(BaseModel):
    id: int
    keyword: str
    results: Optional[List[Any]] = None
    created_at: str

    class Config:
        from_attributes = True

