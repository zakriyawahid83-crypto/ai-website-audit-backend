
import os

import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from database import get_db
from models.user import User

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token. Please log in again.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication configuration is missing.",
        )

    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )

        user_id = payload.get("sub")

        if user_id is None:
            raise credentials_error

        user_id = int(user_id)

    except (InvalidTokenError, ValueError, TypeError):
        raise credentials_error

    user = db.query(User).filter(User.id == user_id).first()

    if user is None:
        raise credentials_error

    return user


def require_admin(
    current_user: User = Depends(get_current_user),
):
    if str(getattr(current_user, "role", "")).lower() != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )

    return current_user
