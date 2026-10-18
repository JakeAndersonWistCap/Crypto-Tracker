"""
fetch/sky_mkr.py — tier 2: Sky's MKR backing and its CURRENT reward-vest rate, read from the contracts (Jake 2026-10-12,
decision 2 on 6f8c86c).

(a) MKR-BACKED SKY HELD BY NON-ISSUERS IS CIRCULATING (the same logic as Morpho's Wrapper): the MKR_SKY converter holds
    SKY pre-minted for unconverted MKR. Its share backing MKR held by everyone else is circulating (they convert at will,
    at rate() x (1 - fee())); the issuer's MKR (the Pause Proxy) and the rest of the converter's SKY stay out. Daily:
        mkr_outstanding_tokens          MKR.totalSupply()
        mkr_issuer_tokens               MKR.balanceOf(Pause Proxy)
        mkr_sky_fee                     converter.fee() / 1e18 (the delayed-upgrade fee; governance can change it)
        sky_mkr_nonissuer_backing_tokens (outstanding - issuer) x converter.rate() x (1 - fee) — added back to Sky's
                                        on-chain circulating (config CIRCULATING_ONCHAIN["Sky"]["add"])
(b) THE CURRENT VEST RATE: the SKY staking rewards are paid by VestedRewardsDistribution from a DssVest award, re-set
    every few weeks (vest 14: 976.8M/yr, 15: 1,162.8M/yr, 16: 393.0M/yr, 17: 580.8M/yr from 2026-09-13). The Q0 measured
    emissions blend them; forward-looking rows use the award in force:
        emissions_rate_current_annual_tokens  awards(vestId()).tot / (fin - bgn) x 365 days — the vest id, its dates in
                                              the source
Addresses: config PROJECT_BY_NAME["Sky"]["mkr_backing"] (read on chain 2026-10-12: rate 24,000, fee 5%).
"""
from __future__ import annotations

import logging

from .base import tidy, today

log = logging.getLogger("token_metrics.fetch.sky_mkr")

SOURCE = "sky_mkr"
TIER = 2
YEAR_S = 365 * 86_400


def _word(h: str | None, i: int = 0) -> int | None:
    if not h or len(h) < 2 + 64 * (i + 1):
        return None
    return int(h[2 + 64 * i: 2 + 64 * (i + 1)], 16)


def read(spec: dict, block: str = "latest") -> dict:
    """{outstanding, issuer, rate, fee, backing, vest: {...}} or {"error": why} — one consistent read at `block`."""
    import check_offline_items as coi                      # noqa: PLC0415 — the probes' eth_call, shared
    sel_bal = coi.SEL_BALANCE_OF

    def call(to, data):
        h, why = coi.eth_call(to, data, block=block, chain="ethereum")
        if h is None:
            raise RuntimeError(f"{to[:10]} {data[:10]}: {why}")
        return h
    try:
        out = {"outstanding": _word(call(spec["mkr"], coi._selx("totalSupply()"))) / 1e18,
               "issuer": _word(call(spec["mkr"], sel_bal + spec["issuer"].lower()[2:].rjust(64, "0"))) / 1e18,
               "rate": _word(call(spec["converter"], coi._selx("rate()"))),
               "fee": _word(call(spec["converter"], coi._selx("fee()"))) / 1e18}
        out["backing"] = (out["outstanding"] - out["issuer"]) * out["rate"] * (1 - out["fee"])
        vid = _word(call(spec["vest_distribution"], coi._selx("vestId()")))
        vest = coi.as_address(call(spec["vest_distribution"], coi._selx("dssVest()")))
        aw = call(vest, coi._selx("awards(uint256)") + hex(vid)[2:].rjust(64, "0"))
        bgn, fin, tot = _word(aw, 1), _word(aw, 3), _word(aw, 6)
        out["vest"] = {"id": vid, "bgn": bgn, "fin": fin, "tot": tot / 1e18,
                       "annual": (tot / 1e18) * YEAR_S / (fin - bgn) if fin and bgn and fin > bgn else None}
        return out
    except Exception as e:  # noqa: BLE001
        from .chain import redact_urls
        return {"error": redact_urls(e)}


def vest_awards(spec: dict, upto: int, call=None) -> list[dict]:
    """Every DssVest award 1..`upto` paying the VestedRewardsDistribution (awards(id).usr), read now from the vest's own
    records — bgn, fin and tot stay on chain after an award ends or is yanked (DssVest._yank sets fin to the yank time and
    tot to what had accrued, so tot / (fin − bgn) is the award's rate either way)."""
    import check_offline_items as coi                      # noqa: PLC0415

    def _call(to, data):
        h, why = coi.eth_call(to, data, block="latest", chain="ethereum")
        if h is None:
            raise RuntimeError(f"{to[:10]} {data[:10]}: {why}")
        return h
    call = call or _call
    vest = coi.as_address(call(spec["vest_distribution"], coi._selx("dssVest()")))
    dist = spec["vest_distribution"].lower()
    out = []
    for i in range(1, int(upto) + 1):
        aw = call(vest, coi._selx("awards(uint256)") + hex(i)[2:].rjust(64, "0"))
        usr = "0x" + aw[2:66][-40:]
        bgn, fin, tot = _word(aw, 1), _word(aw, 3), _word(aw, 6)
        if usr.lower() != dist or not (bgn and fin and fin > bgn):
            continue
        out.append({"id": i, "bgn": bgn, "fin": fin, "tot": tot / 1e18, "annual": (tot / 1e18) * YEAR_S / (fin - bgn)})
    return out


def vest_rate_history(awards: list[dict], days) -> list[tuple]:
    """[(day, annual rate, award)] — for each UTC day, the award in force at the day's CLOSE (23:59:59 UTC; the higher id
    where two overlap: the newer award replaced the older) — the rate a read on that day reports once that day's spell
    has run (vest 17 began 2026-09-13 12:42 UTC, so 2026-09-13 reads vest 17). Days no award covers are left out (no
    rate is inferred for them)."""
    import pandas as pd                                    # noqa: PLC0415
    rows = []
    for d in days:
        d0 = pd.Timestamp(d).tz_localize("UTC") if pd.Timestamp(d).tzinfo is None else pd.Timestamp(d)
        t = int((d0.normalize() + pd.Timedelta(days=1)).timestamp()) - 1
        live = [a for a in awards if a["bgn"] <= t < a["fin"]]
        if live:
            a = max(live, key=lambda x: x["id"])
            rows.append((pd.Timestamp(d), a["annual"], a))
    return rows


VEST_HISTORY_DAYS = 455


class SkyMkr:
    def __init__(self, reader=None, stored_long=None, daily=None, awards_reader=None, **_ignored):
        self.reader = reader or read
        self.stored = stored_long
        self.awards_reader = awards_reader or vest_awards
        if daily is None:
            from .logcache import DailyChecks
            daily = DailyChecks()
        self.daily = daily

    def _vest_history(self, name: str, spec: dict, vest: dict, out) -> None:
        """THE RATE IN FORCE ON PAST DAYS, FROM THE AWARD RECORDS (Round G 2.3: emissions_rate_current_annual_tokens
        started 2026-10-12, so the vest-17 reference of 2026-09-13 — 580.8M SKY/yr — had nothing to check). Only days
        BEFORE the series' first stored row, never over a live read; once a day."""
        import pandas as pd                                # noqa: PLC0415
        metric = "emissions_rate_current_annual_tokens"
        day = str(today().date())
        key = f"{SOURCE}:{name}:vest_history"
        # WITHOUT THE STORE THERE IS NO "BEFORE THE FIRST ROW": a caller that passes no stored_long (a one-off read, a
        # test) gets the live rate only — never 455 days that could land on stored live reads
        if self.stored is None or not self.daily.due(key, day):
            return
        s = self.stored
        first = None
        if s is not None and not getattr(s, "empty", True):
            x = s[(s["project"] == name) & (s["metric"] == metric)]
            if not x.empty:
                first = pd.to_datetime(x["date"]).min().normalize()
        end = first if first is not None else today()
        start = today() - pd.Timedelta(days=VEST_HISTORY_DAYS)
        days = list(pd.date_range(start, end - pd.Timedelta(days=1)))
        if not days:
            self.daily.done(key, day)
            return
        try:
            awards = self.awards_reader(spec, vest["id"])
        except Exception as e:  # noqa: BLE001
            from .chain import redact_urls
            out.fail(SOURCE, name, f"{metric} history: the DssVest award records could not be read — {redact_urls(e)}",
                     TIER)
            return
        rows = vest_rate_history(awards, days)
        if rows:
            by_award: dict = {}
            for d, v, a in rows:
                by_award.setdefault(a["id"], []).append((d, v, a))
            import config                                  # noqa: PLC0415
            for aid, rs in sorted(by_award.items()):
                a = rs[0][2]
                when = (f"vest {aid}: {pd.Timestamp(a['bgn'], unit='s').date()}.."
                        f"{pd.Timestamp(a['fin'], unit='s').date()}, {a['tot']:,.0f} SKY")
                out.add(tidy([(d, float(v)) for d, v, _ in rs], name, metric,
                             config.mark_source(f"{SOURCE}:dss_vest.awards[{when}]", "record"), TIER), SOURCE, name,
                        f"{metric}: {len(rs)} past day(s) {rs[0][0].date()}..{rs[-1][0].date()} = {a['annual']:,.0f}/yr "
                        f"from the award record ({when}) — the award in force at each day's close (UTC)", TIER)
        out.mark_current(SOURCE, name, metric,
                         f"{metric} history: {len(rows)} of {len(days)} day(s) before "
                         f"{(end.date() if hasattr(end, 'date') else end)} covered by {len(awards)} award record(s) "
                         f"paying the distribution; days no award covers are left out", TIER)
        self.daily.done(key, day)

    def run(self, projects: list[dict], window_days, out):
        import pandas as pd                                # noqa: PLC0415
        for p in projects:
            spec = p.get("mkr_backing")
            if not spec:
                continue
            name = p["name"]
            got = self.reader(spec)
            if got.get("error"):
                out.fail(SOURCE, name, f"MKR backing / vest read: {got['error']}", TIER)
                continue
            day = today()
            for metric, v, src in (
                    ("mkr_outstanding_tokens", got["outstanding"], f"{SOURCE}:mkr.totalSupply"),
                    ("mkr_issuer_tokens", got["issuer"], f"{SOURCE}:mkr.balanceOf(pause_proxy)"),
                    ("mkr_sky_fee", got["fee"], f"{SOURCE}:mkr_sky.fee"),
                    ("sky_mkr_nonissuer_backing_tokens", got["backing"],
                     f"{SOURCE}:(mkr.totalSupply-pause_proxy)xrate[{got['rate']}]x(1-fee)")):
                out.add(tidy([(day, float(v))], name, metric, src, TIER), SOURCE, name, f"{metric}={v:,.4f}", TIER)
            vest = got["vest"]
            if vest.get("annual") is not None:
                when = (f"vest {vest['id']}: {pd.Timestamp(vest['bgn'], unit='s').date()}.."
                        f"{pd.Timestamp(vest['fin'], unit='s').date()}, {vest['tot']:,.0f} SKY")
                out.add(tidy([(day, float(vest["annual"]))], name, "emissions_rate_current_annual_tokens",
                             f"{SOURCE}:dss_vest.awards[{when}]", TIER), SOURCE, name,
                        f"emissions_rate_current_annual_tokens={vest['annual']:,.0f}/yr ({when})", TIER)
                self._vest_history(name, spec, vest, out)
            log.info("%s: MKR outstanding %s, issuer %s, rate %s, fee %.4f -> non-issuer backing %s SKY",
                     name, f"{got['outstanding']:,.2f}", f"{got['issuer']:,.2f}", got["rate"], got["fee"],
                     f"{got['backing']:,.0f}")
