"""
E-Trade / Morgan Stanley Gains & Losses CSV parser.

The export (XLSX → CSV via Numbers) uses:
  - ";" as field separator
  - "." as thousands separator
  - "," as decimal separator
  - "US$" prefix scattered at irregular positions (Numbers locale artifact)

Each Sell record carries BOTH acquisition info (Date Acquired, Ordinary Income
Recognized = FMV*qty at vest) AND sale info (Date Sold, Total Proceeds). For
Swedish tax we emit TWO normalized transactions per row: a VEST/PURCHASE on
the acquisition date and a SALE on the sold date. Cost basis in USD =
Ordinary Income Recognized (RSU basis is FMV at vest; ESPP basis is FMV at
purchase — both already income-taxed).
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path


def _parse_usd(s: str) -> float:
    """Strip 'US$' and Swedish locale formatting → float.
    '.US$726,510' → 726.51, 'US$.71,78953' → 71.78953, '.US$,000' → 0.0
    """
    s = (s or "").strip()
    if not s:
        return 0.0
    s = s.replace("US$", "").replace(".", "").replace(",", ".")
    # Possible leftover leading '.' if original was like ',000' → '.000' which float() accepts.
    if s == "" or s == "-":
        return 0.0
    return float(s)


def _parse_qty(s: str) -> float:
    """Swedish-locale number without currency, e.g. '10,12' → 10.12, '21,62' → 21.62."""
    s = (s or "").strip()
    if not s:
        return 0.0
    return float(s.replace(".", "").replace(",", "."))


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s or s == "--":
        return None
    return datetime.strptime(s, "%m/%d/%Y").date()


def parse(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        rows = list(reader)

    txns: list[dict] = []
    for row in rows:
        rec_type = (row.get("Record Type") or "").strip()
        if rec_type != "Sell":
            # Skip Summary row and anything else.
            continue

        symbol = (row.get("Symbol") or "").strip()
        if not symbol:
            raise ValueError(f"E-Trade Sell row has no Symbol: {row}")
        plan = (row.get("Plan Type") or "").strip()         # "RS" or "ESPP"
        type_desc = (row.get("Type") or "").strip()         # "Restricted Stock Unit" / "ESPP"

        qty = _parse_qty(row.get("Quantity", ""))
        if qty == 0:
            continue

        date_acquired = _parse_date(row.get("Date Acquired", ""))
        date_sold = _parse_date(row.get("Date Sold", ""))
        ord_income = _parse_usd(row.get("Ordinary Income Recognized", ""))
        total_proceeds = _parse_usd(row.get("Total Proceeds", ""))

        # Per-share basis = ordinary income recognized / qty. This is FMV at
        # acquisition for both RSU and ESPP and is the Swedish cost basis.
        basis_per_share = ord_income / qty if qty else 0.0
        sale_per_share = total_proceeds / qty if qty else 0.0

        acquisition_type = "PURCHASE" if plan == "ESPP" else "VEST"
        source_tag = f"E-Trade {type_desc or plan} Grant={row.get('Grant Number', '').strip()}"

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
            "usd_fees": 0.0,  # E-Trade Sell records carry no explicit fee column.
            "source": f"E-Trade Sell Order={row.get('Order Number', '').strip()}",
        })

    return txns
