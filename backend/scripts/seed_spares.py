"""Seed spare_parts with 18 realistic rows across 5 categories.

Run: python scripts/seed_spares.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SEED_ROWS = [
    # Bearings (lead_time_days=21)
    {
        "part_name": "Deep Groove Ball Bearing 6205-2RS",
        "category": "bearing",
        "qty_on_hand": 8,
        "reorder_threshold": 3,
        "lead_time_days": 21,
        "unit_cost_usd": "42.50",
        "supplier": "SKF Industrial",
    },
    {
        "part_name": "Cylindrical Roller Bearing NJ2210",
        "category": "bearing",
        "qty_on_hand": 4,
        "reorder_threshold": 2,
        "lead_time_days": 21,
        "unit_cost_usd": "118.00",
        "supplier": "SKF Industrial",
    },
    {
        "part_name": "Thrust Bearing 51106",
        "category": "bearing",
        "qty_on_hand": 2,
        "reorder_threshold": 2,
        "lead_time_days": 21,
        "unit_cost_usd": "67.00",
        "supplier": "FAG Bearings",
    },
    {
        "part_name": "Spherical Roller Bearing 22212-E1",
        "category": "bearing",
        "qty_on_hand": 0,
        "reorder_threshold": 1,
        "lead_time_days": 28,
        "unit_cost_usd": "195.00",
        "supplier": "FAG Bearings",
    },
    # Seals (lead_time_days=14)
    {
        "part_name": "Mechanical Seal Kit Type 21 (20mm)",
        "category": "seal",
        "qty_on_hand": 6,
        "reorder_threshold": 2,
        "lead_time_days": 14,
        "unit_cost_usd": "85.00",
        "supplier": "John Crane",
    },
    {
        "part_name": "O-Ring Set NBR 70 Shore (assorted)",
        "category": "seal",
        "qty_on_hand": 12,
        "reorder_threshold": 4,
        "lead_time_days": 5,
        "unit_cost_usd": "18.00",
        "supplier": "Parker Hannifin",
    },
    {
        "part_name": "Lip Seal Double 40x55x7",
        "category": "seal",
        "qty_on_hand": 3,
        "reorder_threshold": 2,
        "lead_time_days": 14,
        "unit_cost_usd": "22.50",
        "supplier": "Parker Hannifin",
    },
    {
        "part_name": "Pump Shaft Seal Assembly 1.5in",
        "category": "seal",
        "qty_on_hand": 1,
        "reorder_threshold": 2,
        "lead_time_days": 21,
        "unit_cost_usd": "145.00",
        "supplier": "John Crane",
    },
    # Couplings (lead_time_days=14)
    {
        "part_name": "Flexible Jaw Coupling L090",
        "category": "coupling",
        "qty_on_hand": 3,
        "reorder_threshold": 1,
        "lead_time_days": 14,
        "unit_cost_usd": "78.00",
        "supplier": "Rexnord",
    },
    {
        "part_name": "Coupling Insert Spider (polyurethane) L090",
        "category": "coupling",
        "qty_on_hand": 6,
        "reorder_threshold": 2,
        "lead_time_days": 7,
        "unit_cost_usd": "32.00",
        "supplier": "Rexnord",
    },
    {
        "part_name": "Rigid Flange Coupling 40mm",
        "category": "coupling",
        "qty_on_hand": 2,
        "reorder_threshold": 1,
        "lead_time_days": 14,
        "unit_cost_usd": "210.00",
        "supplier": "Lovejoy",
    },
    # Lubricants / oils (lead_time_days=3)
    {
        "part_name": "Shell Tellus S2 M46 Hydraulic Oil (20L)",
        "category": "lubricant",
        "qty_on_hand": 10,
        "reorder_threshold": 3,
        "lead_time_days": 3,
        "unit_cost_usd": "95.00",
        "supplier": "Shell Lubricants",
    },
    {
        "part_name": "Mobil Grease XHP 222 (5kg)",
        "category": "lubricant",
        "qty_on_hand": 5,
        "reorder_threshold": 2,
        "lead_time_days": 3,
        "unit_cost_usd": "48.00",
        "supplier": "ExxonMobil",
    },
    {
        "part_name": "Castrol Optigear Synthetic 320 (5L)",
        "category": "oil",
        "qty_on_hand": 4,
        "reorder_threshold": 2,
        "lead_time_days": 3,
        "unit_cost_usd": "62.00",
        "supplier": "Castrol Industrial",
    },
    {
        "part_name": "ISO VG 46 Compressor Oil (4L)",
        "category": "oil",
        "qty_on_hand": 8,
        "reorder_threshold": 3,
        "lead_time_days": 3,
        "unit_cost_usd": "28.00",
        "supplier": "Shell Lubricants",
    },
    # Thermal / gaskets (lead_time_days=10)
    {
        "part_name": "Spiral Wound Gasket DN50 PN16",
        "category": "thermal gasket",
        "qty_on_hand": 5,
        "reorder_threshold": 2,
        "lead_time_days": 10,
        "unit_cost_usd": "35.00",
        "supplier": "Flexitallic",
    },
    {
        "part_name": "Full-Face Gasket EPDM 80mm",
        "category": "thermal gasket",
        "qty_on_hand": 8,
        "reorder_threshold": 3,
        "lead_time_days": 7,
        "unit_cost_usd": "12.00",
        "supplier": "Flexitallic",
    },
    {
        "part_name": "Heat Exchanger Gasket Set (plate type)",
        "category": "thermal gasket",
        "qty_on_hand": 1,
        "reorder_threshold": 1,
        "lead_time_days": 14,
        "unit_cost_usd": "320.00",
        "supplier": "Tranter",
    },
]


async def seed():
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text

    upsert_sql = """
        INSERT INTO spare_parts
          (part_name, category, qty_on_hand, reorder_threshold, lead_time_days,
           unit_cost_usd, supplier, last_updated)
        VALUES
          (:part_name, :category, :qty_on_hand, :reorder_threshold, :lead_time_days,
           :unit_cost_usd, :supplier, now())
        ON CONFLICT (part_name, supplier) DO UPDATE SET
          category         = EXCLUDED.category,
          qty_on_hand      = EXCLUDED.qty_on_hand,
          reorder_threshold = EXCLUDED.reorder_threshold,
          lead_time_days   = EXCLUDED.lead_time_days,
          unit_cost_usd    = EXCLUDED.unit_cost_usd,
          last_updated     = now()
    """

    async with AsyncSessionLocal() as db:
        for row in _SEED_ROWS:
            await db.execute(text(upsert_sql), row)
        await db.commit()

        count = (await db.execute(text("SELECT count(*) FROM spare_parts"))).scalar_one()
        print(f"spare_parts count: {count}")

        sample = await db.execute(
            text("SELECT part_name, category, qty_on_hand, lead_time_days, unit_cost_usd FROM spare_parts ORDER BY category, part_name LIMIT 6")
        )
        print("\nSample rows:")
        for r in sample.fetchall():
            print(f"  {r[1]:15s}  {r[0][:40]:40s}  qty={r[2]:3d}  lead={r[3]:2d}d  ${r[4]}")


if __name__ == "__main__":
    asyncio.run(seed())
