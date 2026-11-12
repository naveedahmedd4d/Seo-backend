"""Custom Image Search helpers used for social post images."""

from app.services.custom_image_search_service import (
    DEFAULT_BASE_URL,
    _normalize_images,
    normalize_base_url,
)


def test_normalize_base_url_default():
    assert normalize_base_url(None) == DEFAULT_BASE_URL
    assert normalize_base_url("") == DEFAULT_BASE_URL
    assert normalize_base_url("https://blog-booster.vercel.app/") == (
        "https://blog-booster.vercel.app"
    )


def test_normalize_base_url_strips_search_path():
    assert normalize_base_url(
        "https://blog-booster.vercel.app/api/image-search"
    ) == "https://blog-booster.vercel.app"


def test_normalize_images_maps_full_size_url():
    images = _normalize_images(
        {
            "images": [
                {
                    "title": "A",
                    "full_size_url": "https://cdn.example/a.jpg",
                    "thumbnail_url": "https://cdn.example/a-thumb.jpg",
                    "source": "blog-booster",
                }
            ]
        }
    )
    assert images == [
        {
            "title": "A",
            "link": "https://cdn.example/a.jpg",
            "original": "https://cdn.example/a.jpg",
            "thumbnail": "https://cdn.example/a-thumb.jpg",
            "source": "blog-booster",
        }
    ]
