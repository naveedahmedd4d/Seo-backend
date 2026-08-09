from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class KeywordSiteCoverage(Base):
    __tablename__ = "keyword_site_coverage"
    __table_args__ = (
        UniqueConstraint(
            "wordpress_site_id",
            "trending_keyword_id",
            name="uq_keyword_site_coverage_site_keyword",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    wordpress_site_id = Column(
        Integer,
        ForeignKey("wordpress_sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trending_keyword_id = Column(
        Integer,
        ForeignKey("trending_keywords.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String, nullable=False, index=True)  # existing | new
    matched_url = Column(String, nullable=True)
    matched_title = Column(String, nullable=True)
    match_method = Column(String, nullable=True)  # title_exact | title_contains | slug_exact | slug_contains
    compared_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    wordpress_site = relationship("WordPressSite")
    trending_keyword = relationship("TrendingKeywords")
