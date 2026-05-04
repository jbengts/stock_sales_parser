"""
E-Trade / Morgan Stanley Gains & Losses XLSX parser.

The native xlsx export carries proper numeric and date types, so no
Swedish-locale unmangling is needed (unlike the older CSV-via-Numbers path).

Each Sell record carries BOTH acquisition info (Date Acquired, Ordinary Income
Recognized = FMV*qty at vest) AND sale info (Date Sold, Total Proceeds). For
Swedish tax we emit TWO normalized transactions per row: a VEST/PURCHASE on
the acquisition date and a SALE on the sold date. Cost basis in USD =
Ordinary Income Recognized (RSU basis is FMV at vest; ESPP basis is FMV at
purchase — both already income-taxed).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import openpyxl


def _to_float(v) -> float:
    if v is None or v == "" or v == "--":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s == "--":
        return 0.0
    return float(s)


def _to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s or s == "--":
        return None
    return datetime.strptime(s, "%m/%d/%Y").date()


def _to_str(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def parse(path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = [_to_str(c) for c in next(rows_iter)]
    rows = [dict(zip(header, row)) for row in rows_iter]

    txns: list[dict] = []
    for row in rows:
        rec_type = _to_str(row.get("Record Type"))
        if rec_type != "Sell":
            continue

        symbol = _to_str(row.get("Symbol"))
        if not symbol:
            raise ValueError(f"E-Trade Sell row has no Symbol: {row}")
        plan = _to_str(row.get("Plan Type"))           # "RS" or "ESPP"
        type_desc = _to_str(row.get("Type"))           # "Restricted Stock Unit" / "ESPP"

        qty = _to_float(row.get("Quantity"))
        if qty == 0:
            continue

        date_acquired = _to_date(row.get("Date Acquired"))
        date_sold = _to_date(row.get("Date Sold"))
        ord_income = _to_float(row.get("Ordinary Income Recognized"))
        total_proceeds = _to_float(row.get("Total Proceeds"))

        basis_per_share = ord_income / qty if qty else 0.0
        sale_per_share = total_proceeds / qty if qty else 0.0

        acquisition_type = "PURCHASE" if plan == "ESPP" else "VEST"
        source_tag = f"E-Trade {type_desc or plan} Grant={_to_str(row.get('Grant Number'))}"

        txns.append({
            "date": date_acquired,
            "type": acquisition_type,
            "broker": "etrade",
            "symbol": symbol,
            "qty": qty,
            "usd_per_share": basis_per_share,
            "usd_fees": 0.0,
            "source": source_tag,
        })

        txns.append({
            "date": date_sold,
            "type": "SALE",
            "broker": "etrade",
            "symbol": symbol,
            "qty": qty,
            "usd_per_share": sale_per_share,
            "usd_fees": 0.0,
            "source": f"E-Trade Sell Order={_to_str(row.get('Order Number'))}",
        })

    return txns
