
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database import Base, engine
from models import User, Website, AuditJob, AuditIssue

from routers.auth import router as auth_router
from routers.websites import router as websites_router
from routers.audits import router as audits_router
from routers.admin import router as admin_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database tables when the API starts."""
    try:
        Base.metadata.create_all(bind=engine)
        print("Database tables initialized successfully.")
    except SQLAlchemyError as exc:
        print(f"Database initialization failed: {exc}")
        raise

    yield

    print("AI Website Audit API shutting down.")


app = FastAPI(
    title="AI Website Audit Platform",
    description=(
        "API for website SEO, performance, accessibility, "
        "content analysis, and optimization reports."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------
# CORS CONFIGURATION
# --------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------
# ROOT ENDPOINT
# --------------------------------------------------

@app.get("/", tags=["General"])
def home():
    return {
        "success": True,
        "message": "AI Website Audit API is running",
        "version": "1.0.0",
        "docs": "/docs",
    }


# --------------------------------------------------
# API HEALTH CHECK
# --------------------------------------------------

@app.get("/health", tags=["Health"])
def health_check():
    return {
        "success": True,
        "status": "healthy",
        "service": "AI Website Audit API",
    }


# --------------------------------------------------
# DATABASE HEALTH CHECK
# --------------------------------------------------

@app.get("/health/database", tags=["Health"])
def database_health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return {
            "success": True,
            "database": "connected",
        }

    except SQLAlchemyError as exc:
        print(f"Database health check failed: {exc}")

        raise HTTPException(
            status_code=503,
            detail="Database connection failed.",
        )


# --------------------------------------------------
# REGISTER ROUTERS
# --------------------------------------------------

app.include_router(auth_router)
app.include_router(websites_router)
app.include_router(audits_router)
app.include_router(admin_router)


# --------------------------------------------------
# APPLICATION INFORMATION
# --------------------------------------------------

@app.get("/api/info", tags=["General"])
def api_info():
    return {
        "name": "AI Website Audit Platform",
        "version": "1.0.0",
        "features": [
            "User authentication",
            "Website management",
            "Website audits",
            "Audit history",
            "SEO analysis",
            "Performance optimization",
            "Accessibility checks",
        ],
    }