
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from models.website import Website
from schemas.website import WebsiteCreate, WebsiteResponse
from dependencies.auth import get_current_user

router = APIRouter(prefix="/websites", tags=["Websites"])


@router.post(
    "/",
    response_model=WebsiteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_website(
    data: WebsiteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = Website(
        url=str(data.url),
        name=data.name,
        user_id=current_user.id,
    )

    db.add(website)
    db.commit()
    db.refresh(website)

    return website


@router.get("/", response_model=list[WebsiteResponse])
def list_websites(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(Website)
        .filter(Website.user_id == current_user.id)
        .order_by(Website.id.desc())
        .all()
    )


@router.delete("/{website_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_website(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = (
        db.query(Website)
        .filter(
            Website.id == website_id,
            Website.user_id == current_user.id,
        )
        .first()
    )

    if website is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Website not found",
        )

    db.delete(website)
    db.commit()