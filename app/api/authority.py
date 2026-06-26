from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.authority_site import AuthorityNodeStatus
from app.models.user import User
from app.schemas.authority import (
    AuthoritySiteCreate,
    AuthoritySiteResponse,
    AuthoritySiteSummaryResponse,
    BlogCreate,
    BlogUpdate,
    ClusterCreate,
    ClusterUpdate,
    ContentUpdate,
    GenerateRequest,
    MoneyPageUpdate,
    PublishRequest,
)
from app.services import authority_service
from app.services.authority_content_service import (
    bg_run_generate_blog,
    bg_run_generate_cluster_blogs,
    bg_run_generate_money_page,
    bg_run_generate_service_page,
)
from app.services.authority_publish_service import (
    publish_blog_post,
    publish_money_page,
    publish_ready_nodes,
    publish_service_page,
    unpublish_node,
)

router = APIRouter()


def _parse_status(value: str | None) -> AuthorityNodeStatus | None:
    if value is None:
        return None
    return AuthorityNodeStatus(value)


def _wp_status(body: PublishRequest) -> str:
    return "publish" if body.status == "publish" else "draft"


@router.get("/sites", response_model=List[AuthoritySiteSummaryResponse])
async def list_sites(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sites = authority_service.list_authority_sites(db, current_user.id)
    return [authority_service.serialize_site_summary(s) for s in sites]


@router.post("/sites", response_model=AuthoritySiteResponse, status_code=201)
async def create_site(
    body: AuthoritySiteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.create_authority_site(
        db,
        current_user.id,
        body.wordpress_site_id,
        body.money_page_title,
        body.money_page_focus_keyword,
        body.money_page_slug,
    )
    return authority_service.serialize_site(site)


@router.get("/sites/{site_id}", response_model=AuthoritySiteResponse)
async def get_site(
    site_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.get_authority_site(db, site_id, current_user.id)
    return authority_service.serialize_site(site)


@router.put("/sites/{site_id}/money-page", response_model=AuthoritySiteResponse)
async def update_money_page(
    site_id: int,
    body: MoneyPageUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.update_money_page(
        db,
        site_id,
        current_user.id,
        title=body.title,
        slug=body.slug,
        focus_keyword=body.focus_keyword,
        status=_parse_status(body.status),
    )
    return authority_service.serialize_site(site)


@router.put("/sites/{site_id}/money-page/content", response_model=AuthoritySiteResponse)
async def update_money_page_content(
    site_id: int,
    body: ContentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.update_money_page_content(
        db,
        site_id,
        current_user.id,
        body=body.body,
        meta_description=body.meta_description,
        seo_title=body.seo_title,
    )
    return authority_service.serialize_site(site)


@router.post("/sites/{site_id}/money-page/generate", response_model=AuthoritySiteResponse)
async def generate_money_page(
    site_id: int,
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.get_authority_site(db, site_id, current_user.id)
    if site.money_page_is_generating:
        raise HTTPException(status_code=409, detail="Money page generation already in progress")
    site.money_page_is_generating = True
    site.money_page_generation_error = None
    db.commit()
    background_tasks.add_task(
        bg_run_generate_money_page, site_id, current_user.id, body.include_research
    )
    site = authority_service.get_authority_site(db, site_id, current_user.id)
    return authority_service.serialize_site(site)


@router.post("/sites/{site_id}/money-page/publish", response_model=AuthoritySiteResponse)
async def publish_money_page_endpoint(
    site_id: int,
    body: PublishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = await publish_money_page(db, site_id, current_user.id, _wp_status(body))
    return authority_service.serialize_site(site)


@router.post("/sites/{site_id}/publish-ready", response_model=AuthoritySiteResponse)
async def publish_ready(
    site_id: int,
    body: PublishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = await publish_ready_nodes(db, site_id, current_user.id, _wp_status(body))
    return authority_service.serialize_site(site)


@router.post("/sites/{site_id}/clusters", response_model=AuthoritySiteResponse, status_code=201)
async def create_cluster(
    site_id: int,
    body: ClusterCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    blogs = None
    if body.blogs:
        blogs = [
            {
                "title": b.title,
                "focus_keyword": b.focus_keyword or b.title,
                "article_type": b.article_type,
            }
            for b in body.blogs
        ]
    site = authority_service.create_cluster(
        db,
        site_id,
        current_user.id,
        body.title,
        body.focus_keyword,
        body.slug,
        blogs,
    )
    return authority_service.serialize_site(site)


@router.put("/clusters/{cluster_id}", response_model=AuthoritySiteResponse)
async def update_cluster(
    cluster_id: int,
    body: ClusterUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.update_cluster(
        db,
        cluster_id,
        current_user.id,
        title=body.title,
        slug=body.slug,
        focus_keyword=body.focus_keyword,
        status=_parse_status(body.status),
    )
    return authority_service.serialize_site(site)


@router.put("/clusters/{cluster_id}/content", response_model=AuthoritySiteResponse)
async def update_cluster_content(
    cluster_id: int,
    body: ContentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.update_cluster_content(
        db,
        cluster_id,
        current_user.id,
        body=body.body,
        meta_description=body.meta_description,
        seo_title=body.seo_title,
    )
    return authority_service.serialize_site(site)


@router.post("/clusters/{cluster_id}/generate", response_model=AuthoritySiteResponse)
async def generate_cluster(
    cluster_id: int,
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cluster = authority_service._get_cluster(db, cluster_id, current_user.id)
    if cluster.is_generating:
        raise HTTPException(status_code=409, detail="Service page generation already in progress")
    cluster.is_generating = True
    cluster.generation_error = None
    db.commit()
    background_tasks.add_task(
        bg_run_generate_service_page, cluster_id, current_user.id, body.include_research
    )
    site = authority_service.get_authority_site(db, cluster.authority_site_id, current_user.id)
    return authority_service.serialize_site(site)


@router.post("/clusters/{cluster_id}/generate-blogs", response_model=AuthoritySiteResponse)
async def generate_cluster_blogs(
    cluster_id: int,
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cluster = authority_service._get_cluster(db, cluster_id, current_user.id)
    if any(b.is_generating for b in cluster.blog_posts):
        raise HTTPException(status_code=409, detail="Blog generation already in progress")
    for blog in cluster.blog_posts:
        blog.is_generating = True
        blog.generation_error = None
    db.commit()
    background_tasks.add_task(
        bg_run_generate_cluster_blogs, cluster_id, current_user.id, body.include_research
    )
    site = authority_service.get_authority_site(db, cluster.authority_site_id, current_user.id)
    return authority_service.serialize_site(site)


@router.post("/clusters/{cluster_id}/publish", response_model=AuthoritySiteResponse)
async def publish_cluster(
    cluster_id: int,
    body: PublishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = await publish_service_page(db, cluster_id, current_user.id, _wp_status(body))
    return authority_service.serialize_site(site)


@router.delete("/clusters/{cluster_id}", response_model=AuthoritySiteResponse)
async def delete_cluster(
    cluster_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.delete_cluster(db, cluster_id, current_user.id)
    return authority_service.serialize_site(site)


@router.post("/clusters/{cluster_id}/blogs", response_model=AuthoritySiteResponse, status_code=201)
async def add_blog(
    cluster_id: int,
    body: BlogCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.add_blog(
        db,
        cluster_id,
        current_user.id,
        body.title,
        body.focus_keyword,
        body.slug,
        body.article_type,
    )
    return authority_service.serialize_site(site)


@router.put("/blogs/{blog_id}", response_model=AuthoritySiteResponse)
async def update_blog(
    blog_id: int,
    body: BlogUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.update_blog(
        db,
        blog_id,
        current_user.id,
        title=body.title,
        slug=body.slug,
        focus_keyword=body.focus_keyword,
        status=_parse_status(body.status),
        article_type=body.article_type,
    )
    return authority_service.serialize_site(site)


@router.put("/blogs/{blog_id}/content", response_model=AuthoritySiteResponse)
async def update_blog_content(
    blog_id: int,
    body: ContentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.update_blog_content(
        db,
        blog_id,
        current_user.id,
        body=body.body,
        meta_description=body.meta_description,
        seo_title=body.seo_title,
    )
    return authority_service.serialize_site(site)


@router.post("/blogs/{blog_id}/generate", response_model=AuthoritySiteResponse)
async def generate_blog(
    blog_id: int,
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    blog = authority_service._get_blog(db, blog_id, current_user.id)
    if blog.is_generating:
        raise HTTPException(status_code=409, detail="Blog generation already in progress")
    blog.is_generating = True
    blog.generation_error = None
    db.commit()
    background_tasks.add_task(
        bg_run_generate_blog, blog_id, current_user.id, body.include_research
    )
    site = authority_service.get_authority_site(db, blog.cluster.authority_site_id, current_user.id)
    return authority_service.serialize_site(site)


@router.post("/blogs/{blog_id}/publish", response_model=AuthoritySiteResponse)
async def publish_blog(
    blog_id: int,
    body: PublishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = await publish_blog_post(db, blog_id, current_user.id, _wp_status(body))
    return authority_service.serialize_site(site)


@router.delete("/blogs/{blog_id}", response_model=AuthoritySiteResponse)
async def delete_blog(
    blog_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.delete_blog(db, blog_id, current_user.id)
    return authority_service.serialize_site(site)


@router.post("/published/{published_id}/unpublish", response_model=AuthoritySiteResponse)
async def unpublish_published_node(
    published_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = await unpublish_node(db, published_id, current_user.id)
    return authority_service.serialize_site(site)


@router.post("/sites/{site_id}/rebuild-links", response_model=AuthoritySiteResponse)
async def rebuild_links(
    site_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = authority_service.rebuild_links(db, site_id, current_user.id)
    return authority_service.serialize_site(site)
