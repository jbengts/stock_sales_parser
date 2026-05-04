#!/usr/bin/env python3
"""
K4-deklaration: läser Schwab + E-Trade CSV, producerar K4-rapport i SEK.

Användning:
    python3 deklaration.py
    python3 deklaration.py --year YYYY --schwab inputs/schwab.csv --etrade inputs/etrade.csv

Default --year är föregående kalenderår (det år man deklarerar för).

Output läggs i output/:
    k4_<year>.csv            En rad per försäljning.
    k4_<year>_summary.csv    Summa per symbol (blankettraderna).
    audit_log.csv            Alla transaktioner + FX + poolstatus.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import fx as fx_module
import report
import tax
from brokers import etrade, schwab

HERE = Path(__file__).parent


def merge_same_day(txns: list[dict]) -> list[dict]:
    """Slå ihop transaktioner med samma (datum, symbol, typ, mäklare).

    E-Trades Gains & Losses-export bryter ut ett sälj per anskaffningslott;
    Schwabs vestings delas över AwardId. I genomsnittsmetoden är samma-dag-
    poster ekonomiskt en enda händelse, så sammanslagning gör rapporten
    läsbar utan att påverka sluträkningen (identiska summor).
    """
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for t in txns:
        key = (t["date"], t["symbol"], t["type"], t["broker"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(t)

    merged: list[dict] = []
    for key in order:
        ts = groups[key]
        if len(ts) == 1:
            merged.append(ts[0])
            continue
        total_qty = sum(t["qty"] for t in ts)
        total_usd = sum(t["qty"] * t["usd_per_share"] for t in ts)
        avg_usd = total_usd / total_qty if total_qty else 0.0
        total_fees = sum(t["usd_fees"] for t in ts)
        first = ts[0]
        sources = "; ".join(t["source"] for t in ts)
        merged.append({
            "date": first["date"],
            "type": first["type"],
            "broker": first["broker"],
            "symbol": first["symbol"],
            "qty": total_qty,
            "usd_per_share": avg_usd,
            "usd_fees": total_fees,
            "source": f"[sammanslaget x{len(ts)}] {sources}",
        })
    return merged


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=date.today().year - 1)
    ap.add_argument("--schwab", type=Path, default=HERE / "inputs/schwab.csv")
    ap.add_argument("--etrade", type=Path, default=HERE / "inputs/G&L_Expanded.xlsx")
    ap.add_argument("--output-dir", type=Path, default=HERE / "output")
    args = ap.parse_args()

    args.output_dir.mkdir(exist_ok=True)

    txns: list[dict] = []
    if args.schwab.exists():
        txns += schwab.parse(args.schwab)
        print(f"Schwab: {sum(1 for t in txns if t['broker'] == 'schwab')} transactions")
    else:
        print(f"(no Schwab file at {args.schwab})")

    before = len(txns)
    if args.etrade.exists():
        txns += etrade.parse(args.etrade)
        print(f"E-Trade: {len(txns) - before} transactions")
    else:
        print(f"(no E-Trade file at {args.etrade})")

    if not txns:
        raise SystemExit("No transactions found.")

    before_merge = len(txns)
    txns = merge_same_day(txns)
    if len(txns) < before_merge:
        print(f"Merged {before_merge} → {len(txns)} txns (slog ihop samma-dag-poster)")

    # Sanity check for unsupported types (e.g. dividends) we haven't implemented.
    for t in txns:
        if t["type"] not in ("VEST", "PURCHASE", "SALE"):
            raise SystemExit(
                f"Unsupported transaction type '{t['type']}' on {t['date']} "
                f"({t['source']}). Add handling before producing final report."
            )

    # Fetch Riksbank USD/SEK rates covering full transaction range + one week back
    # (so that a Jan-2 transaction can fall back to Dec-30 rate if needed).
    min_date = min(t["date"] for t in txns)
    max_date = max(t["date"] for t in txns)
    fx = fx_module.build(
        start=date(min_date.year, 1, 1),
        end=date(max_date.year, 12, 31),
    )

    compute = tax.run(txns, fx)

    # Filter sales to the target tax year for reporting.
    compute_year = tax.Compute(
        sales=[s for s in compute.sales if s["date"].year == args.year],
        audit=compute.audit,  # full audit spans all input years
    )

    detail = args.output_dir / f"k4_{args.year}.csv"
    summary = args.output_dir / f"k4_{args.year}_summary.csv"
    audit = args.output_dir / "audit_log.csv"
    explanation = args.output_dir / f"k4_{args.year}_utrakning.txt"
    report.write_k4_detail(detail, compute_year)
    report.write_k4_summary(summary, compute_year)
    report.write_audit_log(audit, compute_year)
    report.write_k4_explanation(explanation, compute_year, args.year)

    print(f"\nWrote {detail}")
    print(f"Wrote {summary}")
    print(f"Wrote {audit}")
    print(f"Wrote {explanation}")

    # Per-sälj sammanfattning på konsolen (en rad per försäljning).
    print(f"\n=== Försäljningar {args.year} — uträkning per sälj ===")
    print(
        "(Genomsnittsmetoden: RSU och ESPP poolas tillsammans per symbol. "
        "Se text-filen ovan för full steg-för-steg-uträkning.)\n"
    )
    sales_sorted = sorted(compute_year.sales, key=lambda s: (s["date"], s["symbol"]))
    for i, s in enumerate(sales_sorted, 1):
        vl = s["vinst_forlust_sek"]
        marker = "vinst" if vl >= 0 else "förlust"
        print(
            f"  [{i}] {s['date']}  {s['symbol']:<6} "
            f"{s['qty']:>8.4f} st  ({s['broker']})"
        )
        print(
            f"      försäljning  = {s['qty']:.4f} × ${s['usd_per_share']:.4f} × "
            f"{s['fx_rate']:.4f} SEK/USD (FX {s['fx_date']})"
        )
        print(
            f"                   = {s['forsaljningspris_sek']:>14,.2f} SEK"
        )
        print(
            "      kostbas (genomsnittsmetoden) — poolens förvärv (kvarvarande efter ev. tidigare sälj):"
        )
        acqs = s.get("acquisitions_before", [])
        for a in acqs:
            label = "RSU " if a["type"] == "VEST" else "ESPP"
            qty_rem = a["qty_remaining"]
            qty_note = ""
            if abs(qty_rem - a["qty"]) > 1e-6:
                qty_note = f" (av {a['qty']:.4f} urspr.)"
            print(
                f"        {a['date']} {label} {qty_rem:>8.4f} st{qty_note}"
                f" × ${a['usd_per_share']:.4f} × {a['fx_rate']:.4f} SEK/USD"
                f" (FX {a['fx_date']}) = {a['sek_per_share']:>8.4f} SEK/aktie"
            )
        # När poolen består av flera förvärv är pool-snittet ett vägt
        # genomsnitt av raderna ovan — värt att visa explicit.
        if len(acqs) > 1:
            print(
                f"      pool-snitt   : {s['pool_qty_before']:.4f} st,"
                f" {s['pool_avg_sek_before']:.4f} SEK/aktie (vägt genomsnitt)"
            )
        print(
            f"      omkostnad    = {s['qty']:.4f} × {s['pool_avg_sek_before']:.4f}"
            f" = {s['omkostnadsbelopp_sek']:>14,.2f} SEK"
        )
        print(
            f"      {marker:<7}      = {vl:>14,.2f} SEK\n"
        )

    # Totaler
    total_fp = sum(s["forsaljningspris_sek"] for s in compute_year.sales)
    total_omk = sum(s["omkostnadsbelopp_sek"] for s in compute_year.sales)
    total_vl = sum(s["vinst_forlust_sek"] for s in compute_year.sales)
    print("=== K4 {yr} totalt ===".format(yr=args.year))
    print(f"  Antal försäljningar : {len(compute_year.sales)}")
    print(f"  Försäljningspris    : {total_fp:,.2f} SEK")
    print(f"  Omkostnadsbelopp    : {total_omk:,.2f} SEK")
    print(f"  Vinst/förlust       : {total_vl:,.2f} SEK")

    # K4-blanketten avsnitt A (marknadsnoterade aktier): en rad per värdepapper,
    # belopp i hela kronor. Skatteverket avrundar varje fält var för sig.
    per_symbol: dict[str, dict] = {}
    for s in compute_year.sales:
        agg = per_symbol.setdefault(s["symbol"], {"antal": 0.0, "fp": 0.0, "omk": 0.0})
        agg["antal"] += s["qty"]
        agg["fp"] += s["forsaljningspris_sek"]
        agg["omk"] += s["omkostnadsbelopp_sek"]

    print(f"\n=== Skriv detta på K4-blanketten (avsnitt A), år {args.year} ===")
    print("(En rad per beteckning. Belopp i hela kronor.)\n")
    print("Avsnitt A — en rad per värdepapper:")
    print(f"  {'Antal':>8}  {'Beteckning':<12} {'Försäljningspris':>18} "
          f"{'Omkostnadsbelopp':>18} {'Vinst':>10} {'Förlust':>10}")
    sum_fp = sum_omk = sum_vinst = sum_forlust = 0
    for sym in sorted(per_symbol):
        a = per_symbol[sym]
        antal = round(a["antal"])
        fp = round(a["fp"])
        omk = round(a["omk"])
        vl = fp - omk
        vinst = vl if vl > 0 else 0
        forlust = -vl if vl < 0 else 0
        sum_fp += fp
        sum_omk += omk
        sum_vinst += vinst
        sum_forlust += forlust
        print(f"  {antal:>8d}  {sym:<12} {fp:>18,d} {omk:>18,d} "
              f"{vinst:>10,d} {forlust:>10,d}".replace(",", " "))

    print("\nSummeringsfälten under avsnitt A (fyll i totalerna):")
    print(f"  7.1 Summa försäljningspris : {sum_fp:>10,d} kr".replace(",", " "))
    print(f"  7.2 Summa omkostnadsbelopp : {sum_omk:>10,d} kr".replace(",", " "))
    print(f"  7.3 Summa vinst            : {sum_vinst:>10,d} kr".replace(",", " "))
    print(f"  7.4 Summa förlust          : {sum_forlust:>10,d} kr".replace(",", " "))


if __name__ == "__main__":
    main()
