"""
USD/SEK mittkurs från Riksbankens SWEA-API.

Serie: SEKUSDPMI (SEK per 1 USD, dagligt mittpris).
Cachas lokalt i cache/riksbank_usdsek.json för reproducerbarhet.
För helg/helgdag används närmast föregående publicerade bankdag.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date, timedelta
from pathlib import Path

SERIES = "SEKUSDPMI"
API = "https://api.riksbank.se/swea/v1/Observations/{series}/{start}/{end}"
CACHE_FILE = Path(__file__).parent / "cache" / "riksbank_usdsek.json"


def _fetch(start: date, end: date) -> dict[str, float]:
    url = API.format(series=SERIES, start=start.isoformat(), end=end.isoformat())
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    return {row["date"]: float(row["value"]) for row in data}


def load_rates(start: date, end: date) -> dict[str, float]:
    """Return {iso-date: rate} covering [start, end]. Cached on disk.

    Cache stores both the observed rates and the requested date range so we can
    tell whether a refetch is needed (rates dict alone is ambiguous about
    requested range because weekends/holidays have no observations)."""
    CACHE_FILE.parent.mkdir(exist_ok=True)
    rates: dict[str, float] = {}
    covered_start: date | None = None
    covered_end: date | None = None
    if CACHE_FILE.exists():
        blob = json.loads(CACHE_FILE.read_text())
        rates = blob.get("rates", {})
        if blob.get("covered_start"):
            covered_start = date.fromisoformat(blob["covered_start"])
        if blob.get("covered_end"):
            covered_end = date.fromisoformat(blob["covered_end"])

    if covered_start and covered_end and covered_start <= start and covered_end >= end:
        return rates

    fetch_start = min(start, covered_start) if covered_start else start
    fetch_end = max(end, covered_end) if covered_end else end
    fetched = _fetch(fetch_start, fetch_end)
    rates.update(fetched)

    new_start = fetch_start
    new_end = fetch_end
    CACHE_FILE.write_text(json.dumps({
        "covered_start": new_start.isoformat(),
        "covered_end": new_end.isoformat(),
        "rates": rates,
    }, sort_keys=True, indent=2))
    return rates


class FX:
    def __init__(self, rates: dict[str, float]):
        self._rates = rates

    def rate_for(self, d: date) -> tuple[float, date]:
        """Return (rate, effective_date). Falls back to previous business day if d is not published."""
        cur = d
        for _ in range(14):
            key = cur.isoformat()
            if key in self._rates:
                return self._rates[key], cur
            cur -= timedelta(days=1)
        raise LookupError(f"No Riksbank USD/SEK rate found within 14 days before {d}")


def build(start: date, end: date) -> FX:
    return FX(load_rates(start, end))
