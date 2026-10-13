
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey
)
from sqlalchemy.sql import func

from database import Base


class AuditJob(Base):
    __tablename__ = "audit_jobs"

    id = Column(Integer, primary_key=True, index=True)

    website_id = Column(
        Integer,
        ForeignKey("websites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status = Column(String(20), default="pending", nullable=False)
    overall_score = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)


class AuditIssue(Base):
    __tablename__ = "audit_issues"

    id = Column(Integer, primary_key=True, index=True)

    audit_id = Column(
        Integer,
        ForeignKey("audit_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    page_url = Column(String(2048), nullable=False)
    category = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    severity = Column(String(20), nullable=False)
    description = Column(Text, nullable=True)
    recommendation = Column(Text, nullable=True)
    evidence = Column(Text, nullable=True)