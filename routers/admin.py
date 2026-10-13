
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, func, inspect as sa_inspect
from sqlalchemy.orm import Session

from database import get_db
from dependencies.auth import require_admin
from models import User, AuditJob

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(require_admin)],
)


def serialize_model(obj):
    """Serialize mapped database columns safely."""
    mapper = sa_inspect(type(obj))
    result = {}

    for column in mapper.columns:
        value = getattr(obj, column.key, None)

        if isinstance(value, (datetime, date)):
            value = value.isoformat()

        result[column.key] = value

    return result


def model_columns(model):
    return list(sa_inspect(model).columns)


def first_column(model, *names):
    available = {column.key for column in model_columns(model)}

    for name in names:
        if name in available:
            return name

    return None


@router.get("/overview")
def admin_overview(db: Session = Depends(get_db)):
    """Return dashboard totals and audit status counts."""
    total_users = db.query(func.count(User.id)).scalar() or 0
    total_audits = db.query(func.count()).select_from(AuditJob).scalar() or 0

    status_column = first_column(AuditJob, "status", "state")
    status_counts = {}

    if status_column:
        column = getattr(AuditJob, status_column)

        rows = (
            db.query(column, func.count())
            .group_by(column)
            .all()
        )

        status_counts = {
            str(status or "unknown"): count
            for status, count in rows
        }

    return {
        "total_users": total_users,
        "total_audits": total_audits,
        "status_counts": status_counts,
    }


@router.get("/users")
def admin_users(
    search: Optional[str] = Query(default=None, max_length=200),
    role: Optional[str] = Query(default=None, max_length=20),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Search and paginate users without returning password hashes."""
    query = db.query(User)

    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                User.name.ilike(pattern),
                User.email.ilike(pattern),
            )
        )

    if role:
        query = query.filter(User.role == role.strip().lower())

    total = query.count()

    users = (
        query.order_by(User.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return {
        "items": [
            {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "created_at": (
                    user.created_at.isoformat()
                    if user.created_at
                    else None
                ),
            }
            for user in users
        ],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/audits")
def admin_audits(
    search: Optional[str] = Query(default=None, max_length=200),
    status: Optional[str] = Query(default=None, max_length=50),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Search and paginate audit jobs using available model fields."""
    query = db.query(AuditJob)
    available = {column.key for column in model_columns(AuditJob)}

    if status:
        status_field = first_column(AuditJob, "status", "state")

        if status_field:
            query = query.filter(
                getattr(AuditJob, status_field) == status
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="AuditJob model has no status field.",
            )

    if search:
        pattern = f"%{search.strip()}%"
        searchable = []

        for column in model_columns(AuditJob):
            if getattr(column.type, "python_type", None) is str:
                searchable.append(
                    getattr(AuditJob, column.key).ilike(pattern)
                )

        if searchable:
            query = query.filter(or_(*searchable))

    date_field = first_column(
        AuditJob, "created_at", "started_at", "id"
    )

    if date_field:
        query = query.order_by(
            getattr(AuditJob, date_field).desc()
        )

    total = query.count()
    jobs = query.offset(skip).limit(limit).all()

    return {
        "items": [serialize_model(job) for job in jobs],
        "total": total,
        "skip": skip,
        "limit": limit,
    }
