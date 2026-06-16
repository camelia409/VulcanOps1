"""
PDF Service — generates a downloadable PDF for a stored role report.

Uses ReportLab (pure Python, no system dependencies) so it works in any
environment where the backend runs.
"""

from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models.report_batch import ReportBatch


_ROLE_TITLES = {
    "engineer": "Engineer Report",
    "supervisor": "Supervisor Report",
    "manager": "Manager Report",
}


def generate_pdf(
    machine_name: str,
    role: str,
    content: str,
    generated_at: datetime | None = None,
) -> bytes:
    """
    Return PDF bytes for one role report.

    Args:
        machine_name: Human-readable machine name for the header.
        role: One of 'engineer', 'supervisor', 'manager'.
        content: The report body text (may contain multiple lines).
        generated_at: Optional generation timestamp to display.

    Returns:
        PDF document bytes.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=1 * inch,
        bottomMargin=1 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Heading1"],
        fontSize=18,
        textColor=colors.HexColor("#1f2937"),
        spaceAfter=12,
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#6b7280"),
        spaceAfter=18,
    )
    body_style = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontSize=11,
        leading=16,
        spaceAfter=12,
    )

    story: list = []

    # Header title
    role_title = _ROLE_TITLES.get(role, role.title())
    story.append(Paragraph(f"{role_title}: {machine_name}", title_style))

    # Metadata line
    meta_parts = [f"Role: {role.title()}"]
    if generated_at:
        meta_parts.append(f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M %Z')}")
    story.append(Paragraph(" | ".join(meta_parts), subtitle_style))

    # Separator line implemented as a thin table
    story.append(Spacer(1, 6))
    story.append(
        Table(
            [[""]],
            colWidths=[6.5 * inch],
            rowHeights=[1],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#d1d5db")),
                    ("LINEBELOW", (0, 0), (-1, -1), 1, colors.HexColor("#d1d5db")),
                ]
            ),
        )
    )
    story.append(Spacer(1, 18))

    # Body content — split on paragraphs to preserve spacing
    for paragraph in content.split("\n\n"):
        cleaned = paragraph.strip()
        if not cleaned:
            continue
        # Preserve single line breaks inside a paragraph by replacing with <br/>
        cleaned = cleaned.replace("\n", "<br/>")
        story.append(Paragraph(cleaned, body_style))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def generate_pdf_from_batch(
    batch: ReportBatch,
    role: str,
    content: str,
) -> bytes:
    """
    Convenience wrapper that derives the machine name from a ReportBatch row.

    Args:
        batch: The ReportBatch instance (must have machine relationship loaded
               or at least full_report_json with machine.machine_name).
        role: One of 'engineer', 'supervisor', 'manager'.
        content: The report body text.

    Returns:
        PDF document bytes.
    """
    machine_name = "Unknown Machine"
    if batch.full_report_json:
        machine_name = (
            batch.full_report_json.get("machine", {}).get("machine_name")
            or machine_name
        )
    return generate_pdf(
        machine_name=machine_name,
        role=role,
        content=content,
        generated_at=batch.generated_at,
    )
