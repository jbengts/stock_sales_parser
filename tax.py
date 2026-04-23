"""
Genomsnittsmetoden + USD→SEK-konvertering per transaktion.

Alla förvärv (VEST + PURCHASE) läggs i en pool per symbol. Vid varje SALE räknas
omkostnadsbelopp ut som (aktuellt snitt SEK/aktie) × antal sålda aktier.

Ordning per datum: förvärv före försäljning (så att samma-dag-förvärv är med i
poolen när säljet processas — typiskt "vest & sell same day").

Transaction-dict (input):
  {date, type: VEST|PURCHASE|SALE, broker, symbol, qty, usd_per_share, usd_fees, source}

SaleResult (output, en per SALE):
  {date, symbol, qty, broker, source,
   usd_per_share, fx_rate, fx_date,
   forsaljningspris_sek, omkostnadsbelopp_sek, vinst_forlust_sek,
   pool_qty_before, pool_avg_sek_before,
   acquisitions_before: [ {date, type, broker, qty, usd_per_share, fx_rate,
                           fx_date, sek_per_share, source}, ... ]}

AuditEntry (en per transaktion):
  {date, type, broker, symbol, qty, usd_per_share, usd_fees,
   fx_rate, fx_date,
   sek_per_share, sek_total, sek_fees,
   pool_qty_after, pool_sek_total_after, pool_avg_sek_after, source}
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fx import FX


_TYPE_ORDER = {"VEST": 0, "PURCHASE": 0, "SALE": 1}


@dataclass
class Pool:
    qty: float = 0.0
    sek_total_cost: float = 0.0

    @property
    def avg_sek(self) -> float:
        return self.sek_total_cost / self.qty if self.qty else 0.0


@dataclass
class Compute:
    sales: list[dict] = field(default_factory=list)
    audit: list[dict] = field(default_factory=list)


def run(transactions: list[dict], fx: FX) -> Compute:
    # Sort: by date ascending; on same date acquisitions before sales.
    txns = sorted(transactions, key=lambda t: (t["date"], _TYPE_ORDER[t["type"]]))

    pools: dict[str, Pool] = {}
    # Running list of all acquisitions per symbol, kept for explanation output.
    # Not modified by sales (genomsnittsmetoden averages the pool — individual
    # acquisitions lose identity in the cost basis but are useful to display).
    acquisitions: dict[str, list[dict]] = {}
    out = Compute()

    for t in txns:
        sym = t["symbol"]
        pool = pools.setdefault(sym, Pool())

        rate, fx_date = fx.rate_for(t["date"])
        sek_per_share = t["usd_per_share"] * rate
        sek_fees = t["usd_fees"] * rate

        if t["type"] in ("VEST", "PURCHASE"):
            sek_total = sek_per_share * t["qty"] + sek_fees
            pool.qty += t["qty"]
            pool.sek_total_cost += sek_total
            acquisitions.setdefault(sym, []).append({
                "date": t["date"],
                "type": t["type"],
                "broker": t["broker"],
                "qty": t["qty"],            # ursprungligt antal (oförändrat)
                "qty_remaining": t["qty"],  # minskas pro-rata vid varje sälj
                "usd_per_share": t["usd_per_share"],
                "fx_rate": rate,
                "fx_date": fx_date,
                "sek_per_share": sek_per_share,
                "source": t["source"],
            })

        elif t["type"] == "SALE":
            if t["qty"] > pool.qty + 1e-6:
                raise ValueError(
                    f"Sale of {t['qty']} {sym} on {t['date']} exceeds pool "
                    f"of {pool.qty}. Missing acquisition records?"
                )
            pool_qty_before = pool.qty
            pool_avg_before = pool.avg_sek
            # Snapshot BEFORE we mutate the pool / acquisitions list below.
            # Shallow-copy each acquisition dict so later pro-rata mutation of
            # qty_remaining doesn't affect the snapshot attached to this sale.
            acquisitions_before = [dict(a) for a in acquisitions.get(sym, [])]

            forsaljningspris = sek_per_share * t["qty"] - sek_fees
            omkostnad = pool_avg_before * t["qty"]
            vinst = forsaljningspris - omkostnad

            # Reduce pool proportionally (genomsnittsmetoden: pro-rata cost removal).
            pool.sek_total_cost -= omkostnad
            pool.qty -= t["qty"]
            # Guard against float drift driving pool slightly negative/positive.
            if abs(pool.qty) < 1e-6:
                pool.qty = 0.0
                pool.sek_total_cost = 0.0
                # Poolen är tömd — rensa förvärvshistoriken så att framtida
                # sälj inte visar sedan-sålda förvärv som del av sin kostbas.
                # Nya förvärv startar en fräsch pool.
                acquisitions[sym] = []
            elif pool_qty_before > 0:
                # Partiellt sälj: reducera varje förvärvs "qty_remaining"
                # pro-rata enligt (pool efter / pool före). SEK/aktie för
                # förvärvet är oförändrat — bara kvarvarande antal minskar.
                ratio = pool.qty / pool_qty_before
                for acq in acquisitions.get(sym, []):
                    acq["qty_remaining"] *= ratio

            out.sales.append({
                "date": t["date"],
                "symbol": sym,
                "qty": t["qty"],
                "broker": t["broker"],
                "source": t["source"],
                "usd_per_share": t["usd_per_share"],
                "usd_fees": t["usd_fees"],
                "fx_rate": rate,
                "fx_date": fx_date,
                "forsaljningspris_sek": forsaljningspris,
                "omkostnadsbelopp_sek": omkostnad,
                "vinst_forlust_sek": vinst,
                "pool_qty_before": pool_qty_before,
                "pool_avg_sek_before": pool_avg_before,
                "acquisitions_before": acquisitions_before,
            })

        out.audit.append({
            "date": t["date"],
            "type": t["type"],
            "broker": t["broker"],
            "symbol": sym,
            "qty": t["qty"],
            "usd_per_share": t["usd_per_share"],
            "usd_fees": t["usd_fees"],
            "fx_rate": rate,
            "fx_date": fx_date,
            "sek_per_share": sek_per_share,
            "sek_fees": sek_fees,
            "pool_qty_after": pool.qty,
            "pool_sek_total_after": pool.sek_total_cost,
            "pool_avg_sek_after": pool.avg_sek,
            "source": t["source"],
        })

    return out
