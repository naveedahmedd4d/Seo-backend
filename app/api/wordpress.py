from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.wordpress_site import WordPressSite
from app.services.wordpress_service import (
    get_user_wordpress_sites,
    get_wordpress_site,
    create_wordpress_site,
    update_wordpress_site,
    delete_wordpress_site,
    test_wordpress_connection,
    publish_to_wordpress,
)
from app.schemas.wordpress import (
    WordPressSiteCreate,
    WordPressSiteUpdate,
    WordPressSiteResponse,
    WordPressSiteTest,
    WordPressTestResponse,
    WordPressPublishRequest,
    WordPressPublishResponse,
)

router = APIRouter()


@router.get("/sites", response_model=List[WordPressSiteResponse])
async def list_wordpress_sites(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all WordPress sites for the current user"""
    sites = get_user_wordpress_sites(db, current_user.id)
    return [
        WordPressSiteResponse(
            id=site.id,
            name=site.name,
            url=site.url,
            username=site.username,
            created_at=site.created_at.isoformat() if site.created_at else ""
        )
        for site in sites
    ]


@router.post("/sites", response_model=WordPressSiteResponse, status_code=status.HTTP_201_CREATED)
async def create_site(
    site_data: WordPressSiteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add a new WordPress site"""
    try:
        # Check for duplicate URL for this user
        existing = db.query(WordPressSite).filter(
            WordPressSite.user_id == current_user.id,
            WordPressSite.url == site_data.url
        ).first()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A WordPress site with this URL already exists"
            )
        
        site = create_wordpress_site(
            db,
            current_user.id,
            site_data.name,
            site_data.url,
            site_data.username,
            site_data.api_key
        )
        # Convert to response model with proper datetime serialization
        return WordPressSiteResponse(
            id=site.id,
            name=site.name,
            url=site.url,
            username=site.username,
            created_at=site.created_at.isoformat() if site.created_at else ""
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating WordPress site: {str(e)}"
        )


@router.get("/sites/{site_id}", response_model=WordPressSiteResponse)
async def get_site(
    site_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific WordPress site"""
    site = get_wordpress_site(db, site_id, current_user.id)
    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WordPress site not found"
        )
    return WordPressSiteResponse(
        id=site.id,
        name=site.name,
        url=site.url,
        username=site.username,
        created_at=site.created_at.isoformat() if site.created_at else ""
    )


@router.put("/sites/{site_id}", response_model=WordPressSiteResponse)
async def update_site(
    site_id: int,
    site_data: WordPressSiteUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a WordPress site"""
    site = get_wordpress_site(db, site_id, current_user.id)
    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WordPress site not found"
        )
    updated_site = update_wordpress_site(
        db,
        site,
        name=site_data.name,
        url=site_data.url,
        username=site_data.username,
        api_key=site_data.api_key
    )
    return WordPressSiteResponse(
        id=updated_site.id,
        name=updated_site.name,
        url=updated_site.url,
        username=updated_site.username,
        created_at=updated_site.created_at.isoformat() if updated_site.created_at else ""
    )


@router.delete("/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(
    site_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a WordPress site"""
    site = get_wordpress_site(db, site_id, current_user.id)
    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WordPress site not found"
        )
    delete_wordpress_site(db, site)
    return None


@router.post("/sites/{site_id}/test", response_model=WordPressTestResponse)
async def test_site_connection(
    site_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Test connection to a WordPress site"""
    site = get_wordpress_site(db, site_id, current_user.id)
    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WordPress site not found"
        )
    
    from app.services.wordpress_service import decrypt_api_key
    decrypted_key = decrypt_api_key(site.api_key)
    result = await test_wordpress_connection(site.url, site.username, decrypted_key)
    return result


@router.post("/test", response_model=WordPressTestResponse)
async def test_connection(
    test_data: WordPressSiteTest
):
    """Test WordPress connection without saving"""
    result = await test_wordpress_connection(
        test_data.url,
        test_data.username,
        test_data.api_key
    )
    return result


@router.post("/publish", response_model=WordPressPublishResponse)
async def publish_content(
    publish_data: WordPressPublishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Publish content to WordPress"""
    site = get_wordpress_site(db, publish_data.site_id, current_user.id)
    if not site:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WordPress site not found"
        )
    
    result = await publish_to_wordpress(
        site,
        publish_data.title,
        publish_data.content,
        publish_data.status
    )
    return result

