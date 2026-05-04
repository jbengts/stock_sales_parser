# stock_sales_parser
<<<<<<< HEAD
A parser for RSU/ESPP stock sales via Schwab or E-trade

# prerequisites
Make sure that the file(s) etrade.csv and/or schwab.csv are in a folder inputs/ in the project root directory

# usage

Parser for RSU/ESPP stock sales from Schwab and E-Trade / Morgan Stanley,
producing a Swedish K4 tax report (in SEK) using the *genomsnittsmetoden*
(average cost basis method).

Schwab and E-Trade exports are normalized into VEST / PURCHASE / SALE
transactions, converted to SEK using Riksbanken's daily USD/SEK fix, and
poolade per symbol so each sale gets a weighted-average cost basis.

## Inputs

Place broker exports in `inputs/`:

- `inputs/schwab.csv` — Schwab "Equity Award Center" CSV export.
- `inputs/G&L_Expanded.xlsx` — E-Trade / Morgan Stanley "Gains & Losses Expanded" XLSX export.

## Setup

```sh
pip install -r requirements.txt
```

Requires Python 3.10+ (uses `X | Y` type unions).

## Usage

```sh
python3 deklaration.py
```

Defaults to the previous calendar year. Override:

```sh
python3 deklaration.py --year 2025 \
    --schwab inputs/schwab.csv \
    --etrade inputs/G&L_Expanded.xlsx
```

## Output

Written to `output/`:

- `k4_<year>.csv` — one row per sale (K4 detail).
- `k4_<year>_summary.csv` — totals per symbol (K4 form rows).
- `k4_<year>_utrakning.txt` — step-by-step calculation per sale.
- `audit_log.csv` — all transactions with FX rates and pool state.

Riksbanken FX rates are cached under `cache/`.
>>>>>>> 6925ce8 (use xlsx for E-trade)
