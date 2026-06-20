"""CSV ingestion for spare_parts table.

Expected columns (case-insensitive, order-independent):
  part_name, category, qty_on_hand, reorder_threshold,
  lead_time_days, unit_cost_usd, supplier
"""

from __future__ import annotations

import csv
import io
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.upload_response import UploadResponse

_REQUIRED_COLS = {"part_name", "lead_time_days"}
_OPTIONAL_COLS = {
    "category", "qty_on_hand", "reorder_threshold",
    "unit_cost_usd", "supplier",
}


def _parse_int(val: str, default: int = 0) -> int:
    try:
        return int(val.strip())
    except (ValueError, AttributeError):
        return default


def _parse_decimal(val: str) -> str | None:
    if not val or not val.strip():
        return None
    try:
        float(val.strip())
        return val.strip()
    except ValueError:
        return None


async def ingest_spares(content: bytes, filename: str, db: AsyncSession) -> UploadResponse:
    text_data = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text_data))

    if reader.fieldnames is None:
        return UploadResponse(
            status="error",
            filename=filename,
            rows_processed=0,
            rows_inserted=0,
            rows_failed=0,
            errors=["CSV has no header row"],
        )

    headers = {h.strip().lower() for h in reader.fieldnames}
    missing = _REQUIRED_COLS - headers
    if missing:
        return UploadResponse(
            status="error",
            filename=filename,
            rows_processed=0,
            rows_inserted=0,
            rows_failed=0,
            errors=[f"Missing required columns: {', '.join(sorted(missing))}"],
        )

    upsert_sql = text("""
        INSERT INTO spare_parts
          (part_name, category, qty_on_hand, reorder_threshold, lead_time_days,
           unit_cost_usd, supplier, last_updated)
        VALUES
          (:part_name, :category, :qty_on_hand, :reorder_threshold, :lead_time_days,
           :unit_cost_usd, :supplier, now())
        ON CONFLICT (part_name, supplier) DO UPDATE SET
          category          = EXCLUDED.category,
          qty_on_hand       = EXCLUDED.qty_on_hand,
          reorder_threshold = EXCLUDED.reorder_threshold,
          lead_time_days    = EXCLUDED.lead_time_days,
          unit_cost_usd     = EXCLUDED.unit_cost_usd,
          last_updated      = now()
    """)

    rows_ok = 0
    rows_fail = 0
    errors: list[str] = []

    for i, raw_row in enumerate(reader, start=2):
        row: dict[str, Any] = {k.strip().lower(): v.strip() for k, v in raw_row.items() if k}

        part_name = row.get("part_name", "").strip()
        if not part_name:
            errors.append(f"Row {i}: part_name is empty — skipped")
            rows_fail += 1
            continue

        lead_raw = row.get("lead_time_days", "").strip()
        if not lead_raw:
            errors.append(f"Row {i}: lead_time_days is empty — skipped")
            rows_fail += 1
            continue

        try:
            lead_time = int(lead_raw)
        except ValueError:
            errors.append(f"Row {i}: lead_time_days='{lead_raw}' is not an integer — skipped")
            rows_fail += 1
            continue

        params = {
            "part_name": part_name,
            "category": row.get("category") or None,
            "qty_on_hand": _parse_int(row.get("qty_on_hand", "0")),
            "reorder_threshold": _parse_int(row.get("reorder_threshold", "0")),
            "lead_time_days": lead_time,
            "unit_cost_usd": _parse_decimal(row.get("unit_cost_usd", "")),
            "supplier": row.get("supplier") or None,
        }

        try:
            await db.execute(upsert_sql, params)
            rows_ok += 1
        except Exception as exc:
            errors.append(f"Row {i}: DB error — {exc}")
            rows_fail += 1

    await db.commit()

    return UploadResponse(
        status="ok" if rows_fail == 0 else "partial",
        filename=filename,
        rows_processed=rows_ok + rows_fail,
        rows_inserted=rows_ok,
        rows_failed=rows_fail,
        errors=errors[:20],
    )
