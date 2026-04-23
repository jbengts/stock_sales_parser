"""
Schwab CSV parser. Input is a multi-row format where master rows (Date+Action
present) are followed by zero or more detail rows (Date+Action empty) that carry
lot/award metadata for that master.

Emits normalized Transaction dicts:
  {date, type, broker, symbol, qty, usd_per_share, usd_fees, source}

type ∈ {VEST, PURCHASE, SALE}. For Swedish tax, `date` is the *acquisition*
date — VestDate for RSU, PurchaseDate for ESPP — not the deposit date into
the brokerage account.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path


def _parse_money(s: str) -> float | None:
    s = (s or "").strip().replace(",", "").replace("$", "")
    if not s:
        return None
    return float(s)


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    return datetime.strptime(s, "%m/%d/%Y").date()


def _group_master_details(rows: list[dict]) -> list[tuple[dict, list[dict]]]:
    """Group rows into (master, [details]) tuples. A row is a master if it has
    both Date and Action."""
    groups: list[tuple[dict, list[dict]]] = []
    current: tuple[dict, list[dict]] | None = None
    for row in rows:
        if row.get("Date", "").strip() and row.get("Action", "").strip():
            if current is not None:
                groups.append(current)
            current = (row, [])
        else:
            if current is None:
                # Orphan detail row (shouldn't happen in practice); skip.
                continue
            current[1].append(row)
    if current is not None:
        groups.append(current)
    return groups


def parse(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    txns: list[dict] = []
    for master, details in _group_master_details(rows):
        action = master["Action"].strip()
        desc = master["Description"].strip()
        mdate = _parse_date(master["Date"])
        symbol = master["Symbol"].strip()

        if action == "Wire Transfer":
            # Cash movement, not a K4 event.
            continue

        if not symbol:
            raise ValueError(f"Schwab row on {mdate} ({action}) has no Symbol")

        if action == "Deposit" and desc == "RS":
            # RSU vesting. Detail row carries VestDate + VestFairMarketValue + AwardId.
            if not details:
                raise ValueError(f"Deposit RS on {mdate} missing detail row")
            d = details[0]
            vest_date = _parse_date(d["VestDate"]) or mdate
            fmv = _parse_money(d["VestFairMarketValue"])
            qty = float(master["Quantity"])
            txns.append({
                "date": vest_date,
                "type": "VEST",
                "broker": "schwab",
                "symbol": symbol,
                "qty": qty,
                "usd_per_share": fmv,
                "usd_fees": 0.0,
                "source": f"Schwab Deposit RS AwardId={d.get('AwardId', '').strip()}",
            })
            continue

        if action == "Deposit" and desc == "ESPP":
            # ESPP purchase. Detail row carries PurchaseDate + PurchaseFairMarketValue.
            if not details:
                raise ValueError(f"Deposit ESPP on {mdate} missing detail row")
            d = details[0]
            pdate = _parse_date(d["PurchaseDate"]) or mdate
            fmv = _parse_money(d["PurchaseFairMarketValue"])
            qty = float(master["Quantity"])
            txns.append({
                "date": pdate,
                "type": "PURCHASE",
                "broker": "schwab",
                "symbol": symbol,
                "qty": qty,
                "usd_per_share": fmv,
                "usd_fees": 0.0,
                "source": f"Schwab Deposit ESPP PurchaseDate={pdate}",
            })
            continue

        if action in ("Sale", "Quick Sale"):
            # Schwab's detail-row GrossProceeds/TotalCostBasis are IRS-oriented
            # (e.g. PurchasePrice*qty for disqualified ESPP). Master `Amount`
            # is the actual net cash received; gross = Amount + fees.
            fees = _parse_money(master["FeesAndCommissions"]) or 0.0
            total_qty = float(master["Quantity"])
            amt = _parse_money(master["Amount"]) or 0.0
            gross = amt + fees
            usd_per_share = gross / total_qty if total_qty else 0.0
            txns.append({
                "date": mdate,
                "type": "SALE",
                "broker": "schwab",
                "symbol": symbol,
                "qty": total_qty,
                "usd_per_share": usd_per_share,
                "usd_fees": fees,
                "source": f"Schwab {action}",
            })
            continue

        # Unknown action — surface it rather than silently skipping.
        raise ValueError(f"Unknown Schwab action '{action}' ({desc}) on {mdate}")

    return txns
