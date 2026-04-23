"""CSV-rapporter: K4-detalj, K4-summa per symbol, och audit-logg."""

from __future__ import annotations

import csv
from pathlib import Path

from tax import Compute


def _fmt(v: float, decimals: int = 2) -> str:
    return f"{v:.{decimals}f}"


def write_k4_detail(path: Path, compute: Compute) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "date", "symbol", "antal",
            "forsaljningspris_SEK", "omkostnadsbelopp_SEK", "vinst_forlust_SEK",
            "usd_per_share", "fx_rate", "fx_date",
            "broker", "source",
        ])
        for s in compute.sales:
            w.writerow([
                s["date"], s["symbol"], _fmt(s["qty"], 4),
                _fmt(s["forsaljningspris_sek"]),
                _fmt(s["omkostnadsbelopp_sek"]),
                _fmt(s["vinst_forlust_sek"]),
                _fmt(s["usd_per_share"], 5),
                _fmt(s["fx_rate"], 5),
                s["fx_date"],
                s["broker"], s["source"],
            ])


def write_k4_summary(path: Path, compute: Compute) -> None:
    """Aggregera per symbol — det är detta som skrivs på K4-blankettens rad."""
    per_symbol: dict[str, dict] = {}
    for s in compute.sales:
        agg = per_symbol.setdefault(s["symbol"], {
            "antal": 0.0, "fp": 0.0, "omk": 0.0, "vl": 0.0,
        })
        agg["antal"] += s["qty"]
        agg["fp"] += s["forsaljningspris_sek"]
        agg["omk"] += s["omkostnadsbelopp_sek"]
        agg["vl"] += s["vinst_forlust_sek"]

    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "symbol", "antal",
            "forsaljningspris_SEK", "omkostnadsbelopp_SEK", "vinst_forlust_SEK",
        ])
        for sym, a in sorted(per_symbol.items()):
            w.writerow([
                sym, _fmt(a["antal"], 4),
                _fmt(a["fp"]), _fmt(a["omk"]), _fmt(a["vl"]),
            ])


def _fmt_sek(v: float) -> str:
    # Space as thousands separator, two decimals — svensk tusentalsavgränsare.
    s = f"{v:,.2f}"
    return s.replace(",", " ")


_ACQ_LABEL = {"VEST": "RSU-vest", "PURCHASE": "ESPP-köp"}


def write_k4_explanation(path: Path, compute: Compute, year: int) -> None:
    """Skriv en människovänlig uträkning per försäljning.

    Syftet är att ge en spårbar redogörelse inför deklarationen: vilka förvärv
    har matat poolen, vilken genomsnittskurs (SEK/aktie) gäller, och hur
    försäljningspris och vinst/förlust räknas fram.
    """
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"K4-uträkning för år {year} (genomsnittsmetoden)")
    lines.append("=" * 72)
    lines.append("")
    lines.append(
        "Genomsnittsmetoden enligt Skatteverket: alla förvärv av samma aktie\n"
        "— oavsett om de kommer från RSU-vesting (VEST), ESPP-köp (PURCHASE)\n"
        "eller privat marknadsköp — läggs i EN gemensam pool. Omkostnads-\n"
        "beloppet vid varje försäljning beräknas som poolens genomsnittliga\n"
        "SEK/aktie × antal sålda aktier.\n\n"
        "RSU och ESPP behandlas alltså INTE var för sig i denna uträkning;\n"
        "de blandas i samma pool. (Den skatterättsliga skillnaden ligger i\n"
        "att förmånsvärdet redan är löneinkomstbeskattat vid förvärvet;\n"
        "kostbasen i pool:en = FMV på vest-/köp-datumet, omräknat till SEK\n"
        "med Riksbankens USD/SEK-kurs den dagen.)"
    )
    lines.append("")

    if not compute.sales:
        lines.append(f"Inga försäljningar under {year}.")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    sales_sorted = sorted(compute.sales, key=lambda s: (s["date"], s["symbol"]))
    total = len(sales_sorted)

    for i, s in enumerate(sales_sorted, 1):
        lines.append("-" * 72)
        lines.append(f"Försäljning {i} av {total}")
        lines.append("-" * 72)
        lines.append(f"Datum:       {s['date']}")
        lines.append(f"Aktie:       {s['symbol']}")
        lines.append(f"Antal sålda: {s['qty']:.4f}")
        lines.append(f"Mäklare:     {s['broker']}")
        lines.append(f"Källrad:     {s['source']}")
        lines.append("")

        # Försäljningspris
        gross_sek = s["usd_per_share"] * s["qty"] * s["fx_rate"]
        fees_sek = s["usd_fees"] * s["fx_rate"]
        lines.append("FÖRSÄLJNINGSPRIS (SEK)")
        lines.append(
            f"  Pris per aktie (USD):   {s['usd_per_share']:.5f}"
        )
        lines.append(
            f"  Växelkurs (Riksbanken {s['fx_date']}): {s['fx_rate']:.5f} SEK/USD"
        )
        lines.append(
            f"  Brutto: {s['qty']:.4f} × {s['usd_per_share']:.5f} × {s['fx_rate']:.5f}"
            f" = {_fmt_sek(gross_sek)} SEK"
        )
        if fees_sek:
            lines.append(
                f"  Avgifter: {s['usd_fees']:.4f} USD × {s['fx_rate']:.5f}"
                f" = {_fmt_sek(fees_sek)} SEK"
            )
            lines.append(
                f"  Netto försäljningspris:  {_fmt_sek(s['forsaljningspris_sek'])} SEK"
            )
        else:
            lines.append(
                f"  Försäljningspris (inga avgifter): {_fmt_sek(s['forsaljningspris_sek'])} SEK"
            )
        lines.append("")

        # Poolens tillstånd och acquisitions
        lines.append("OMKOSTNADSBELOPP (genomsnittsmetoden)")
        acqs = s.get("acquisitions_before", [])
        if acqs:
            rsu_qty = sum(a["qty_remaining"] for a in acqs if a["type"] == "VEST")
            espp_qty = sum(a["qty_remaining"] for a in acqs if a["type"] == "PURCHASE")
            lines.append(
                f"  Poolens sammansättning direkt före försäljningen"
                f" ({rsu_qty:.4f} RSU + {espp_qty:.4f} ESPP"
                f" = {rsu_qty + espp_qty:.4f} aktier kvarvarande):"
            )
            lines.append("")
            for j, a in enumerate(acqs, 1):
                label = _ACQ_LABEL.get(a["type"], a["type"])
                qty_rem = a["qty_remaining"]
                contribution = qty_rem * a["sek_per_share"]
                partial_note = ""
                if abs(qty_rem - a["qty"]) > 1e-6:
                    partial_note = (
                        f" (av {a['qty']:.4f} urspr.; rest pro-rata reducerat"
                        f" av tidigare sälj)"
                    )
                lines.append(
                    f"    [{j}] {a['date']}  {label}  {qty_rem:.4f} aktier"
                    f"{partial_note}"
                )
                lines.append(
                    f"        Pris per aktie (USD):   {a['usd_per_share']:.5f}"
                )
                lines.append(
                    f"        Växelkurs (Riksbanken {a['fx_date']}):"
                    f" {a['fx_rate']:.5f} SEK/USD"
                )
                lines.append(
                    f"        SEK/aktie: {a['usd_per_share']:.5f} × {a['fx_rate']:.5f}"
                    f" = {a['sek_per_share']:.4f} SEK"
                )
                lines.append(
                    f"        Aktuellt bidrag till poolen: {qty_rem:.4f}"
                    f" × {a['sek_per_share']:.4f}"
                    f" = {_fmt_sek(contribution)} SEK"
                )
                lines.append(f"        Källa: {a['source']}")
                lines.append("")
        else:
            lines.append("  (poolen hade inga tidigare förvärv — ovanligt)")

        pool_qty_before = s["pool_qty_before"]
        pool_avg_before = s["pool_avg_sek_before"]
        pool_total_before = pool_qty_before * pool_avg_before

        lines.append("  Poolens tillstånd direkt före försäljning")
        lines.append(
            "  (påverkat av ev. tidigare sälj — genomsnittet består men qty minskas):"
        )
        lines.append(f"    Antal i pool:        {pool_qty_before:.4f}")
        lines.append(f"    Total anskaffning:   {_fmt_sek(pool_total_before)} SEK")
        lines.append(f"    Genomsnitt:          {_fmt_sek(pool_avg_before)} SEK/aktie")
        lines.append("")
        lines.append(
            f"  Omkostnadsbelopp = {s['qty']:.4f} × {_fmt_sek(pool_avg_before)}"
            f" = {_fmt_sek(s['omkostnadsbelopp_sek'])} SEK"
        )
        lines.append("")

        # Resultat
        vl = s["vinst_forlust_sek"]
        label = "VINST" if vl >= 0 else "FÖRLUST"
        sign = "−" if vl < 0 else ""
        lines.append("RESULTAT")
        lines.append(
            f"  {_fmt_sek(s['forsaljningspris_sek'])} − {_fmt_sek(s['omkostnadsbelopp_sek'])}"
            f" = {sign}{_fmt_sek(abs(vl))} SEK  ({label})"
        )
        lines.append("")

    # Totaler
    total_fp = sum(s["forsaljningspris_sek"] for s in sales_sorted)
    total_omk = sum(s["omkostnadsbelopp_sek"] for s in sales_sorted)
    total_vl = sum(s["vinst_forlust_sek"] for s in sales_sorted)
    lines.append("=" * 72)
    lines.append(f"TOTALT {year}")
    lines.append("=" * 72)
    lines.append(f"  Försäljningspris:  {_fmt_sek(total_fp)} SEK")
    lines.append(f"  Omkostnadsbelopp:  {_fmt_sek(total_omk)} SEK")
    lines.append(f"  Vinst/förlust:     {_fmt_sek(total_vl)} SEK")
    lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_audit_log(path: Path, compute: Compute) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "date", "type", "broker", "symbol", "qty",
            "usd_per_share", "usd_fees",
            "fx_rate", "fx_date",
            "sek_per_share", "sek_fees",
            "pool_qty_after", "pool_sek_total_after", "pool_avg_sek_after",
            "source",
        ])
        for a in compute.audit:
            w.writerow([
                a["date"], a["type"], a["broker"], a["symbol"], _fmt(a["qty"], 4),
                _fmt(a["usd_per_share"], 5), _fmt(a["usd_fees"], 4),
                _fmt(a["fx_rate"], 5), a["fx_date"],
                _fmt(a["sek_per_share"], 4), _fmt(a["sek_fees"], 4),
                _fmt(a["pool_qty_after"], 4),
                _fmt(a["pool_sek_total_after"]),
                _fmt(a["pool_avg_sek_after"], 4),
                a["source"],
            ])
