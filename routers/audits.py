
import csv
import io
import ipaddress
import logging
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag
from xml.sax.saxutils import escape

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_db
from dependencies.auth import get_current_user
from models.user import User
from models.website import Website
from models.audit import AuditJob, AuditIssue
from services.ai_recommendations import generate_ai_recommendations


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audits", tags=["Audits"])

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 10.0
MAX_HTML_BYTES = 1_000_000
MAX_LINK_CHECKS = 5
MAX_REDIRECTS = 3


# ---------------------------------------------------------
# URL validation
# ---------------------------------------------------------

def validate_public_url(url: str) -> str:
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(
            status_code=422,
            detail="Please provide a valid HTTP or HTTPS website URL.",
        )

    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=422,
            detail="URLs containing credentials are not allowed.",
        )

    hostname = parsed.hostname.rstrip(".").lower()

    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise HTTPException(
            status_code=422,
            detail="Localhost URLs cannot be audited.",
        )

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except (socket.gaierror, ValueError, OSError) as exc:
        raise HTTPException(
            status_code=422,
            detail="The website hostname could not be resolved.",
        ) from exc

    if not addresses:
        raise HTTPException(
            status_code=422,
            detail="The website hostname could not be resolved.",
        )

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])

        if not ip.is_global:
            raise HTTPException(
                status_code=422,
                detail="The URL resolves to a non-public IP address.",
            )

    return url


# ---------------------------------------------------------
# HTTP fetching
# ---------------------------------------------------------

def fetch_public_response(
    client: httpx.Client,
    url: str,
    max_bytes: int = MAX_HTML_BYTES,
    follow_public_redirects: bool = True,
) -> httpx.Response:
    current_url = url

    for redirect_number in range(MAX_REDIRECTS + 1):
        validate_public_url(current_url)

        with client.stream(
            "GET",
            current_url,
            follow_redirects=False,
        ) as response:

            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")

                if not location:
                    return httpx.Response(
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        request=response.request,
                        content=b"",
                    )

                if not follow_public_redirects:
                    return httpx.Response(
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        request=response.request,
                        content=b"",
                    )

                if redirect_number >= MAX_REDIRECTS:
                    raise ValueError("Too many website redirects.")

                current_url = urljoin(str(response.url), location)
                continue

            content_length = response.headers.get("content-length")

            if content_length:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    declared_length = None

                if declared_length is not None and declared_length > max_bytes:
                    raise ValueError(
                        "The target page exceeds the allowed size."
                    )

            body = bytearray()

            for chunk in response.iter_bytes():
                body.extend(chunk)

                if len(body) > max_bytes:
                    raise ValueError(
                        "The target page exceeds the allowed size."
                    )

            response_headers = dict(response.headers)
            response_headers.pop("content-encoding", None)
            response_headers.pop("content-length", None)
            response_headers.pop("transfer-encoding", None)

            return httpx.Response(
                status_code=response.status_code,
                headers=response_headers,
                content=bytes(body),
                request=response.request,
            )

    raise ValueError("Could not safely follow website redirects.")


def get_public_response(
    client: httpx.Client,
    url: str,
    max_bytes: int = MAX_HTML_BYTES,
):
    try:
        return fetch_public_response(
            client,
            url,
            max_bytes=max_bytes,
        )
    except (httpx.RequestError, ValueError, HTTPException) as exc:
        logger.warning(
            "Could not fetch %s: %s - %s",
            url,
            type(exc).__name__,
            str(exc),
        )
        return None


# ---------------------------------------------------------
# HTML checks
# ---------------------------------------------------------

def find_issues(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    issues = []

    def add_issue(
        category,
        title,
        severity,
        description,
        recommendation,
        evidence="",
    ):
        issues.append({
            "page_url": page_url,
            "category": category,
            "title": title,
            "severity": severity,
            "description": description,
            "recommendation": recommendation,
            "evidence": str(evidence)[:1000],
        })

    title_tag = soup.title
    title = title_tag.get_text(" ", strip=True) if title_tag else ""

    if not title:
        add_issue(
            "SEO", "Missing page title", "High",
            "The page does not have a title element.",
            "Add a unique, descriptive title for this page.",
        )
    elif len(title) < 10:
        add_issue(
            "SEO", "Title may be too short", "Medium",
            f"The title contains {len(title)} characters.",
            "Use a descriptive title that explains the page.", title,
        )
    elif len(title) > 60:
        add_issue(
            "SEO", "Title may be too long", "Medium",
            f"The title contains {len(title)} characters.",
            "Consider shortening the title while preserving its topic.",
            title,
        )

    description_tag = soup.find(
        "meta",
        attrs={"name": lambda value: value and value.lower() == "description"},
    )
    description = (
        description_tag.get("content", "").strip()
        if description_tag else ""
    )

    if not description:
        add_issue(
            "SEO", "Missing meta description", "High",
            "The page does not have a meta description.",
            "Add a concise and relevant description of the page.",
        )
    elif len(description) > 160:
        add_issue(
            "SEO", "Meta description may be too long", "Low",
            f"The description contains {len(description)} characters.",
            "Consider a more concise search-result description.",
            description,
        )

    h1_tags = soup.find_all("h1")

    if not h1_tags:
        add_issue(
            "SEO", "Missing H1 heading", "Medium",
            "No H1 heading was found.",
            "Add a clear primary heading describing the page.",
        )
    elif len(h1_tags) > 1:
        add_issue(
            "SEO", "Multiple H1 headings", "Low",
            f"Found {len(h1_tags)} H1 headings.",
            "Review the heading hierarchy and primary heading.",
            [h.get_text(" ", strip=True) for h in h1_tags[:5]],
        )

    if not soup.find("meta", attrs={"name": "viewport"}):
        add_issue(
            "Mobile", "Missing viewport meta tag", "High",
            "The page does not declare a mobile viewport.",
            "Add a responsive viewport meta tag.",
        )

    if not soup.find("html", attrs={"lang": True}):
        add_issue(
            "Accessibility", "Missing document language", "Medium",
            "The HTML element does not declare a language.",
            "Set an appropriate lang attribute on the HTML element.",
        )

    for image in soup.find_all("img"):
        if image.get("alt") is None:
            add_issue(
                "Accessibility", "Image missing alt attribute", "Medium",
                "An image does not have an alt attribute.",
                "Add alternative text, or empty alt for decorative images.",
                image.get("src", ""),
            )

    for control in soup.find_all(["input", "select", "textarea"]):
        if control.get("type", "").lower() == "hidden":
            continue

        if control.has_attr("aria-label") or control.has_attr("aria-labelledby"):
            continue

        control_id = control.get("id")
        has_label = bool(
            control_id and soup.find("label", attrs={"for": control_id})
        )

        if not has_label:
            add_issue(
                "Accessibility", "Form control may lack a label", "Medium",
                "A form control has no associated label detected by this check.",
                "Associate a visible label or provide an accessible name.",
                str(control)[:300],
            )

    if not soup.find_all("a", href=True):
        add_issue(
            "Content", "No links detected", "Low",
            "No links with href attributes were found.",
            "Check whether page navigation and internal links are present.",
        )

    text_content = soup.get_text(" ", strip=True)

    if len(text_content) < 100:
        add_issue(
            "Content", "Very little text content", "Medium",
            f"Only {len(text_content)} characters of text were detected.",
            "Check whether the page contains enough useful content.",
        )

    canonical = soup.find(
        "link",
        rel=lambda value: value and "canonical" in value,
    )

    if not canonical or not canonical.get("href"):
        add_issue(
            "SEO", "Missing canonical URL", "Low",
            "A canonical link was not detected.",
            "Add a canonical URL if appropriate for this page.",
        )

    if not soup.find("meta", attrs={"property": "og:title"}):
        add_issue(
            "Social", "Missing Open Graph title", "Low",
            "An Open Graph title was not detected.",
            "Consider adding Open Graph metadata for social sharing.",
        )

    return issues


# ---------------------------------------------------------
# robots.txt and sitemap.xml
# ---------------------------------------------------------

def find_site_file_issues(
    client: httpx.Client,
    page_url: str,
) -> list[dict]:
    issues = []
    parsed = urlparse(page_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    for path, label in (
        ("/robots.txt", "robots.txt"),
        ("/sitemap.xml", "sitemap.xml"),
    ):
        response = get_public_response(
            client,
            urljoin(origin, path),
            max_bytes=500_000,
        )

        if response is None:
            issues.append({
                "page_url": page_url,
                "category": "Technical SEO",
                "title": f"Could not check {label}",
                "severity": "Low",
                "description": f"The audit could not retrieve {label}.",
                "recommendation": (
                    "Verify the file is available to search engine crawlers "
                    "when intended."
                ),
                "evidence": "",
            })
            continue

        if response.status_code == 404:
            issues.append({
                "page_url": page_url,
                "category": "Technical SEO",
                "title": f"Missing {label}",
                "severity": "Medium" if label == "sitemap.xml" else "Low",
                "description": f"{label} returned HTTP 404.",
                "recommendation": (
                    "Create and publish this file if appropriate for your website."
                ),
                "evidence": f"HTTP {response.status_code}",
            })
        elif response.status_code >= 400:
            issues.append({
                "page_url": page_url,
                "category": "Technical SEO",
                "title": f"{label} returned an HTTP error",
                "severity": "Low",
                "description": f"{label} returned HTTP {response.status_code}.",
                "recommendation": "Check the file URL and server permissions.",
                "evidence": f"HTTP {response.status_code}",
            })

    return issues


# ---------------------------------------------------------
# Internal link checks
# ---------------------------------------------------------

def find_broken_internal_links(
    html: str,
    page_url: str,
    client: httpx.Client,
) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    page = urlparse(page_url)
    found = set()
    issues = []

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()

        if not href or href.startswith(
            ("#", "mailto:", "tel:", "javascript:")
        ):
            continue

        absolute = urldefrag(urljoin(page_url, href))[0]
        parsed = urlparse(absolute)

        if parsed.scheme not in ("http", "https"):
            continue

        if parsed.netloc.lower() != page.netloc.lower():
            continue

        if absolute in found:
            continue

        found.add(absolute)

        if len(found) > MAX_LINK_CHECKS:
            break

        response = get_public_response(
            client,
            absolute,
            max_bytes=100_000,
        )

        if response is not None and response.status_code >= 400:
            issues.append({
                "page_url": page_url,
                "category": "Links",
                "title": "Broken internal link",
                "severity": "Medium",
                "description": (
                    f"An internal link returned HTTP {response.status_code}."
                ),
                "recommendation": (
                    "Update the link to a working page or remove it if its "
                    "destination no longer exists."
                ),
                "evidence": absolute,
            })

    return issues


# ---------------------------------------------------------
# Scoring
# ---------------------------------------------------------

def calculate_score(issues: list[dict]) -> int:
    penalties = {
        "critical": 20,
        "high": 10,
        "medium": 5,
        "low": 2,
    }

    score = 100

    for issue in issues:
        severity = str(issue.get("severity", "low")).lower()
        score -= penalties.get(severity, 2)

    return max(0, min(100, score))


def calculate_category_scores(issues: list[dict]) -> dict:
    category_aliases = {
        "seo": {"seo", "technical seo"},
        "performance": {"performance"},
        "accessibility": {"accessibility"},
        "content": {"content"},
    }

    penalties = {
        "critical": 20,
        "high": 10,
        "medium": 5,
        "low": 2,
    }

    scores = {}

    for category, aliases in category_aliases.items():
        matching_issues = [
            issue for issue in issues
            if str(issue.get("category", "")).strip().lower() in aliases
        ]

        penalty = sum(
            penalties.get(
                str(issue.get("severity", "low")).lower(),
                2,
            )
            for issue in matching_issues
        )

        scores[category] = max(0, min(100, 100 - penalty))

    # Performance is not measured by the current HTML-only checks.
    scores["performance"] = None

    return scores


# ---------------------------------------------------------
# Database helpers
# ---------------------------------------------------------

def mark_audit_failed(db: Session, audit_id: int, message: str) -> None:
    try:
        db.rollback()

        audit = db.query(AuditJob).filter(
            AuditJob.id == audit_id
        ).first()

        if audit is None:
            return

        audit.status = "failed"
        audit.error_message = message[:2000]
        audit.completed_at = datetime.now(timezone.utc)
        db.commit()

    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Could not save failed audit status for audit_id=%s",
            audit_id,
        )


def get_user_website(
    db: Session,
    website_id: int,
    user_id: int,
) -> Website:
    website = db.query(Website).filter(
        Website.id == website_id,
        Website.user_id == user_id,
    ).first()

    if website is None:
        raise HTTPException(status_code=404, detail="Website not found.")

    return website


def get_user_audit(
    db: Session,
    audit_id: int,
    user_id: int,
) -> AuditJob:
    audit = (
        db.query(AuditJob)
        .join(Website, AuditJob.website_id == Website.id)
        .filter(
            AuditJob.id == audit_id,
            Website.user_id == user_id,
        )
        .first()
    )

    if audit is None:
        raise HTTPException(
            status_code=404,
            detail="Audit report not found.",
        )

    return audit


def get_audit_website(db: Session, audit: AuditJob) -> Website:
    website = db.query(Website).filter(
        Website.id == audit.website_id
    ).first()

    if website is None:
        raise HTTPException(
            status_code=404,
            detail="Website associated with this audit was not found.",
        )

    return website


def audit_to_dict(audit: AuditJob) -> dict:
    return {
        "audit_id": audit.id,
        "website_id": audit.website_id,
        "status": audit.status,
        "overall_score": audit.overall_score,
        "error_message": audit.error_message,
        "created_at": audit.created_at.isoformat() if audit.created_at else None,
        "completed_at": (
            audit.completed_at.isoformat() if audit.completed_at else None
        ),
    }


def issue_to_dict(issue: AuditIssue) -> dict:
    return {
        "id": issue.id,
        "audit_id": issue.audit_id,
        "page_url": issue.page_url,
        "category": issue.category,
        "title": issue.title,
        "severity": issue.severity,
        "description": issue.description,
        "recommendation": issue.recommendation,
        "evidence": issue.evidence,
    }


def get_audit_issues_list(db: Session, audit_id: int) -> list[AuditIssue]:
    return (
        db.query(AuditIssue)
        .filter(AuditIssue.audit_id == audit_id)
        .order_by(AuditIssue.id.asc())
        .all()
    )


# ---------------------------------------------------------
# Run audit: POST /audits/{website_id}
# ---------------------------------------------------------

@router.post("/{website_id}")
def run_audit(
    website_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = get_user_website(db, website_id, current_user.id)

    audit = AuditJob(website_id=website.id, status="running")
    db.add(audit)
    db.commit()
    db.refresh(audit)

    try:
        validate_public_url(website.url)

        with httpx.Client(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "*/*;q=0.8"
                ),
                "Accept-Encoding": "identity",
            },
        ) as client:
            start_time = time.perf_counter()

            response = fetch_public_response(
                client,
                website.url,
                max_bytes=MAX_HTML_BYTES,
            )

            performance_ms = (time.perf_counter() - start_time) * 1000

            if response.status_code >= 400:
                code = response.status_code

                if code == 403:
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            "The target website returned HTTP 403 Forbidden. "
                            "It may restrict automated requests. Try a website "
                            "you own or are authorized to audit."
                        ),
                    )

                if code == 429:
                    raise HTTPException(
                        status_code=429,
                        detail=(
                            "The target website returned HTTP 429 Too Many "
                            "Requests. Wait before retrying."
                        ),
                    )

                raise HTTPException(
                    status_code=502,
                    detail=f"The target website returned HTTP {code}.",
                )

            content_type = response.headers.get("content-type", "").lower()

            if "text/html" not in content_type:
                raise HTTPException(
                    status_code=422,
                    detail="The target URL did not return an HTML page.",
                )

            html = response.content.decode(
                response.encoding or "utf-8",
                errors="replace",
            )
            audited_url = str(response.url)

            detected_issues = find_issues(html, audited_url)
            detected_issues.extend(
                find_site_file_issues(client, audited_url)
            )
            detected_issues.extend(
                find_broken_internal_links(html, audited_url, client)
            )

        for item in detected_issues:
            db.add(AuditIssue(
                audit_id=audit.id,
                page_url=item["page_url"],
                category=item["category"],
                title=item["title"],
                severity=item["severity"],
                description=item["description"],
                recommendation=item["recommendation"],
                evidence=item["evidence"],
            ))

        audit.overall_score = calculate_score(detected_issues)
        audit.status = "completed"
        audit.error_message = None
        audit.completed_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(audit)

        return {
            "audit_id": audit.id,
            "website_id": website.id,
            "status": audit.status,
            "overall_score": audit.overall_score,
            "category_scores": calculate_category_scores(detected_issues),
            "performance_ms": round(performance_ms, 2),
            "issues_found": len(detected_issues),
            "message": "Website audit completed.",
        }

    except HTTPException as exc:
        mark_audit_failed(db, audit.id, str(exc.detail))
        raise

    except (httpx.RequestError, ValueError) as exc:
        logger.exception("Audit request failed for website_id=%s", website_id)
        mark_audit_failed(db, audit.id, f"{type(exc).__name__}: {str(exc)}")

        raise HTTPException(
            status_code=502,
            detail=(
                "The website could not be audited. Check its URL, network "
                "access, and server response."
            ),
        ) from exc

    except Exception as exc:
        logger.exception("Audit failed for website_id=%s", website_id)
        mark_audit_failed(
            db,
            audit.id,
            "Unexpected error while processing the audit.",
        )

        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred while processing the audit.",
        ) from exc


# ---------------------------------------------------------
# Audit history
# ---------------------------------------------------------

@router.get("/history")
def get_audit_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(AuditJob, Website.url)
        .join(Website, AuditJob.website_id == Website.id)
        .filter(Website.user_id == current_user.id)
        .order_by(AuditJob.id.desc())
        .all()
    )

    results = []

    for audit, website_url in rows:
        item = audit_to_dict(audit)
        item["website_url"] = website_url
        results.append(item)

    return {"total": len(results), "audits": results}


# ---------------------------------------------------------
# Issues
# ---------------------------------------------------------

@router.get("/{audit_id}/issues")
def get_audit_issues(
    audit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    audit = get_user_audit(db, audit_id, current_user.id)
    issues = get_audit_issues_list(db, audit.id)

    return {
        "audit_id": audit.id,
        "status": audit.status,
        "total": len(issues),
        "issues": [issue_to_dict(issue) for issue in issues],
    }


# ---------------------------------------------------------
# Full report
# ---------------------------------------------------------

@router.get("/{audit_id}/report")
def get_audit_report(
    audit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    audit = get_user_audit(db, audit_id, current_user.id)
    website = get_audit_website(db, audit)
    issues = get_audit_issues_list(db, audit.id)

    counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}

    for issue in issues:
        severity = (issue.severity or "Low").capitalize()
        if severity in counts:
            counts[severity] += 1

    issue_data = [issue_to_dict(issue) for issue in issues]

    return {
        **audit_to_dict(audit),
        "website_url": website.url,
        "issues_found": len(issues),
        "severity_counts": counts,
        "category_scores": calculate_category_scores(issue_data),
        "issues": issue_data,
    }


# ---------------------------------------------------------
# CSV report
# ---------------------------------------------------------

@router.get("/{audit_id}/report.csv")
def download_audit_csv(
    audit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    audit = get_user_audit(db, audit_id, current_user.id)
    issues = get_audit_issues_list(db, audit.id)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Issue ID", "Page URL", "Category", "Title", "Severity",
        "Description", "Recommendation", "Evidence",
    ])

    for issue in issues:
        writer.writerow([
            issue.id, issue.page_url, issue.category, issue.title,
            issue.severity, issue.description, issue.recommendation,
            issue.evidence,
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition":
                f'attachment; filename="audit-{audit.id}-report.csv"'
        },
    )


# ---------------------------------------------------------
# PDF report
# GET /audits/{audit_id}/report.pdf
# ---------------------------------------------------------

@router.get("/{audit_id}/report.pdf")
def download_audit_pdf(
    audit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    audit = get_user_audit(db, audit_id, current_user.id)
    website = get_audit_website(db, audit)
    issues = get_audit_issues_list(db, audit.id)
    issue_data = [issue_to_dict(issue) for issue in issues]

    category_scores = calculate_category_scores(issue_data)

    buffer = io.BytesIO()
    styles = getSampleStyleSheet()

    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Website Audit Report {audit.id}",
        author="AI Website Audit Platform",
    )

    story = [
        Paragraph("Website Audit Report", styles["Title"]),
        Spacer(1, 5 * mm),
        Paragraph(
            f"<b>Website:</b> {escape(str(website.url))}",
            styles["Normal"],
        ),
        Paragraph(f"<b>Audit ID:</b> {audit.id}", styles["Normal"]),
        Paragraph(
            f"<b>Status:</b> {escape(str(audit.status))}",
            styles["Normal"],
        ),
        Paragraph(
            f"<b>Overall score:</b> "
            f"{audit.overall_score if audit.overall_score is not None else 'N/A'}/100",
            styles["Normal"],
        ),
        Paragraph(
            f"<b>Created:</b> "
            f"{audit.created_at.isoformat() if audit.created_at else 'N/A'}",
            styles["Normal"],
        ),
        Spacer(1, 5 * mm),
        Paragraph("Category Scores", styles["Heading2"]),
    ]

    category_rows = [["Category", "Score", "Meaning"]]

    for key, label in (
        ("seo", "SEO"),
        ("performance", "Performance"),
        ("accessibility", "Accessibility"),
        ("content", "Content"),
    ):
        score = category_scores.get(key)

        if score is None:
            score_text = "Not measured"
            meaning = "No dedicated performance measurement is available."
        else:
            score_text = f"{score}/100"
            meaning = "Estimated from the detected issues in this category."

        category_rows.append([label, score_text, meaning])

    category_table = Table(
        category_rows,
        colWidths=[40 * mm, 35 * mm, 170 * mm],
        repeatRows=1,
    )
    category_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#263449")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5DCE5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white, colors.HexColor("#F4F7FA")
        ]),
        ("PADDING", (0, 0), (-1, -1), 7),
    ]))

    story.extend([
        category_table,
        Spacer(1, 7 * mm),
        Paragraph("Detected Issues", styles["Heading2"]),
    ])

    issue_rows = [[
        "ID", "Category", "Severity", "Issue", "Affected URL",
        "Recommendation",
    ]]

    for issue in issue_data:
        issue_rows.append([
            str(issue.get("id") or ""),
            Paragraph(
                escape(str(issue.get("category") or "General")),
                styles["BodyText"],
            ),
            Paragraph(
                escape(str(issue.get("severity") or "Low")),
                styles["BodyText"],
            ),
            Paragraph(
                escape(str(issue.get("title") or "Untitled issue")),
                styles["BodyText"],
            ),
            Paragraph(
                escape(str(issue.get("page_url") or "")),
                styles["BodyText"],
            ),
            Paragraph(
                escape(str(issue.get("recommendation") or "")),
                styles["BodyText"],
            ),
        ])

    if not issue_data:
        issue_rows.append(["-", "-", "-", "No issues recorded", "-", "-"])

    issue_table = Table(
        issue_rows,
        colWidths=[12 * mm, 27 * mm, 22 * mm, 43 * mm, 65 * mm, 75 * mm],
        repeatRows=1,
    )
    issue_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#263449")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5DCE5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white, colors.HexColor("#F4F7FA")
        ]),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))

    story.append(issue_table)

    document.build(story)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'attachment; filename="audit-{audit.id}-report.pdf"'
        },
    )


# ---------------------------------------------------------
# AI recommendations
# ---------------------------------------------------------

@router.post("/{audit_id}/ai-recommendations")
def get_ai_recommendations(
    audit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    audit = get_user_audit(db, audit_id, current_user.id)
    website = get_audit_website(db, audit)

    if audit.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="AI recommendations require a completed audit.",
        )

    issues = get_audit_issues_list(db, audit.id)

    issue_data = [
        {
            "id": issue.id,
            "page_url": issue.page_url,
            "category": issue.category,
            "title": issue.title,
            "severity": issue.severity,
            "description": issue.description,
            "recommendation": issue.recommendation,
            "evidence": issue.evidence,
        }
        for issue in issues
    ]

    try:
        result = generate_ai_recommendations(issue_data)

        if not isinstance(result, dict):
            raise RuntimeError("AI service returned an invalid response.")

        recommendations = result.get("recommendations", [])

        if not isinstance(recommendations, list):
            raise RuntimeError("AI recommendations must be a list.")

        return {
            "audit_id": audit.id,
            "website_url": website.url,
            "overall_score": audit.overall_score,
            "summary": result.get("summary", ""),
            "recommendations": recommendations,
        }

    except Exception as exc:
        logger.exception(
            "AI recommendations failed for audit_id=%s",
            audit_id,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to generate AI recommendations. "
                "Check the backend logs for details."
            ),
        ) from exc
