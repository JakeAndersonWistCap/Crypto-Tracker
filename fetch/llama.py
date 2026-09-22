"""
fetch/llama.py — tier 1: DefiLlama free endpoints.

Free and unauthenticated: fees, revenue, holders revenue, protocol TVL, chain TVL, stablecoin
supply by chain, and the RWA category aggregated per chain. That is the demand side.

NOT available free, and deliberately not attempted here:
  * emissions / unlocks         — Pro tier, separate API plan
  * per-protocol-version fees   — Pro tier (Uniswap needs fees split by version)
Both are written to the Gap Report rather than approximated.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from .base import Http, tidy, window

log = logging.getLogger("token_metrics.fetch.llama")

SOURCE = "defillama"
# Named here so the skip message and store.ABSENT_RECHECK_DAYS cannot drift apart in prose.
ABSENT_RECHECK_NOTE = "14 days without an attempt (store.ABSENT_RECHECK_DAYS)"
TIER = 1
API = "https://api.llama.fi"
STABLES = "https://stablecoins.llama.fi"

# How the parent/child restructure check reads "recently", and how far the daily level must
# have fallen below what the reported total implies. Blunt on purpose: this is not a sensitivity
# dial for ordinary variation — a listing does not report a month's fees and a near-empty week at
# the same time unless something structural has happened to it.
RESTRUCTURE_RECENT_DAYS = 7
RESTRUCTURE_FACTOR = 10.0

PRO_ONLY = {
    "emissions_tokens": "DefiLlama emissions/unlocks is Pro tier (separate API plan) — not fetched",
    "fees_by_version": "DefiLlama per-version fee breakdown is Pro tier — Uniswap implied burn needs it",
}


class DefiLlama:
    def __init__(self, known_absent: set | None = None):
        self.http = Http(min_interval=0.25)
        self._rwa_by_chain: pd.DataFrame | None = None
        # (source, project) pairs whose endpoint 404'd and has never worked — see
        # store.known_absent. DERIVED FROM THE STORE, not declared in config: the list is
        # whatever has actually been observed, so it cannot go stale against reality and there
        # is no hand-maintained register to disagree with the run log.
        self.known_absent = known_absent or set()

    def _absent(self, name: str, what: str, out) -> bool:
        """True when this pair is a known-absent resource, and the skip is logged as one."""
        if (SOURCE, name) not in self.known_absent:
            return False
        # skipped, NOT failed and NOT unconfigured. It produced no error because no call was
        # made, and it is not a missing config entry — the entry is right and the resource is
        # not there. The Run Log's SKIPPED column is what surfaces it.
        out.skipped(SOURCE, name, f"{what}: KNOWN ABSENT — this endpoint 404'd and has never "
                                  f"returned anything for this project. Not called. It is "
                                  f"retried automatically after "
                                  f"{ABSENT_RECHECK_NOTE}.", TIER)
        return True

    # ------------------------------------------------------------------ fees & revenue
    def _summary(self, slug: str, data_type: str) -> dict:
        return self.http.get(f"{API}/summary/fees/{slug}", params={"dataType": data_type})

    @staticmethod
    def _chart(j: dict):
        return [(datetime.fromtimestamp(ts, tz=timezone.utc), v)
                for ts, v in (j.get("totalDataChart") or [])]

    def _summary_chart(self, slug: str, data_type: str):
        return self._chart(self._summary(slug, data_type))

    # ===== A PARENT WHOSE CHILDREN CARRY THE FEES. Added 2026-09-23. =====
    #
    # ** THE FAILURE THIS CATCHES LEAVES NO ERROR ANYWHERE. ** DefiLlama restructured Morpho on
    # 2026-09-12 into a parent with two children. The parent's slug kept working, kept returning
    # 200, and kept returning a daily series — but that series was no longer the protocol's fees.
    # It became morpho-midnight's alone, to the cent, while morpho-blue's ~$600K/day went to the
    # child. Stored fees fell from ~$575K/day to $2.13, and the only thing that noticed was a
    # human reading the sheet eleven days later.
    #
    # THE ARITHMETIC THAT EXPOSES IT: the parent's own total30d still equals the SUM of its
    # children's — DefiLlama has not lost the money, it has moved where it is reported. So a
    # parent whose DAILY chart no longer sums to its children's daily charts, while its TOTAL
    # still does, has been restructured. Both halves are needed: the totals agreeing is what
    # rules out "the protocol genuinely collapsed", and the dailies disagreeing is what rules out
    # "nothing happened".
    #
    # IT REPORTS, IT DOES NOT REWIRE. Which child to take, and whether to declare a handover, is
    # a decision about what a column MEANS — and a slug swapped automatically would silently
    # redefine a series that already has a year of history under the old definition. The row
    # names the children and their totals so the decision is made against numbers.
    def check_restructure(self, project: dict, out) -> None:
        slug, name = project.get("defillama_fees_slug"), project["name"]
        if not slug or (SOURCE, name) in self.known_absent:
            # A pair the store has already seen 404 and never succeed is not called again here
            # either — the politeness rule is about the endpoint, not about which check wants it.
            return
        if project.get("defillama_restructure"):
            # ALREADY DIAGNOSED. This generic check exists to catch an UNDECLARED restructure —
            # Morpho was the case it was built for. Once one has been investigated and recorded
            # in config, _fees_with_restructure_guard is the specific mechanism that reports and
            # auto-recovers it; running this generic check alongside it would raise a second,
            # redundant flag for the same fact every run.
            return
        try:
            parent = self._summary(slug, "dailyFees")
        except Exception as e:  # noqa: BLE001 — a failed check must not kill the run
            # ONE CAUSE, ONE ROW. fees() has just called the same endpoint and reported the same
            # failure; a second Run Log entry for it would make one dead slug look like two
            # problems. Logged so the check's own silence is explicable, not re-reported.
            log.info("%s: restructure check skipped, %s did not answer (%s)", name, slug, e)
            return

        kids = [str(k) for k in (parent.get("childProtocols") or [])]
        if not kids:
            return
        # A slug that is ITSELF a child is not a restructure — it is the state after one, and
        # config says so. Recorded rather than warned about.
        if parent.get("parentProtocol"):
            log.info("%s: slug %r is a CHILD of %s", name, slug, parent["parentProtocol"])

        parent_30d = float(parent.get("total30d") or 0.0)
        child_30d, child_rows, failed = 0.0, [], []
        for kid in kids:
            try:
                k = self._summary(kid, "dailyFees")
            except Exception as e:  # noqa: BLE001
                failed.append(f"{kid} ({e})")
                continue
            v = float(k.get("total30d") or 0.0)
            child_30d += v
            chart = self._chart(k)
            child_rows.append((kid, v, chart[-1][0].date().isoformat() if chart else None))
        if failed:
            out.fail(SOURCE, name, f"{slug}: {len(failed)} child listing(s) unreadable: "
                                   f"{', '.join(failed)}", TIER)
        if not child_rows or parent_30d <= 0:
            return

        # ===== THE DISCRIMINATOR, AND THE FIRST VERSION OF IT WAS TOO SLOW. =====
        # Comparing the parent's 30-day CHART SUM against its 30-day TOTAL cannot fire until 30
        # days after the break: while the pre-break days are still inside the window the chart
        # sums to ~84% of the total, which is unremarkable. A check that needs a month to notice
        # a month-long problem is not a check. (Its own test caught that.)
        #
        # WHAT IS TRUE FROM THE FIRST DAY is that the parent's RECENT DAILY LEVEL has collapsed
        # while its REPORTED TOTAL has not. Morpho's parent reports $13.05m over 30 days —
        # $434,871/day — and its last seven days sum to about $743, which is $106/day. Those two
        # numbers come from the same listing and cannot both describe it.
        #
        # AND A GENUINE COLLAPSE PASSES THIS, which is the point. If the fees really stopped, the
        # TOTAL falls with the dailies and the two agree again: a protocol at $4,859/30d and
        # $106/day is consistent, unremarkable, and correctly left to level_break.
        pchart = self._chart(parent)
        if not pchart:
            return
        newest = pchart[-1][0]
        recent = sum(v for d, v in pchart if (newest - d).days < RESTRUCTURE_RECENT_DAYS)
        implied = parent_30d / 30.0 * RESTRUCTURE_RECENT_DAYS
        level_gone = recent * RESTRUCTURE_FACTOR < implied
        # THE CHILDREN ARE WHERE THE MONEY WENT, and the totals agreeing is what proves it did
        # not simply stop. Without this a listing whose chart lags its total would be reported as
        # a restructure.
        totals_agree = abs(parent_30d - child_30d) <= max(parent_30d, child_30d) * 0.01
        if not (level_gone and totals_agree):
            return
        parent_recent = recent

        listing = "; ".join(f"{k} 30d={v:,.0f}"
                            + (f", last point {d}" if d else ", NO DAILY POINTS")
                            for k, v, d in sorted(child_rows, key=lambda r: -r[1]))
        out.review_item(
            name, "fees_usd", "source_restructured", "stored_flagged",
            value=parent_recent, prior_value=child_30d,
            date=pchart[-1][0].date().isoformat() if pchart else None,
            basis=(f"{slug!r} is a PARENT with {len(kids)} child listing(s) and has been "
                   f"RESTRUCTURED. Its own total30d ({parent_30d:,.0f}) still equals its "
                   f"children's ({child_30d:,.0f}) — the money has not gone — but its last "
                   f"{RESTRUCTURE_RECENT_DAYS} days of daily chart sum to {parent_recent:,.2f}, "
                   f"against the {implied:,.0f} that total implies. The fees are being reported "
                   f"under the children and the parent's series has become a residual. "
                   f"Children: {listing}. NOT REWIRED AUTOMATICALLY: which child to "
                   f"take, and whether the switch needs a declared handover, decides what this "
                   f"column MEANS, and a slug swapped on a guess silently redefines a series "
                   f"that has history under the old definition."),
            source=f"{SOURCE}:{slug}", tier=TIER)
        out.gap(name, "[data] fees_usd — the DefiLlama listing has been restructured",
                reason=(f"{slug!r} has {len(kids)} children and its daily chart no longer carries "
                        f"their fees, while its 30-day total still matches their sum. "
                        f"Children: {listing}"),
                tiers_attempted="1",
                suggestion=("Run `python llama_probe.py <child> --days 45` on each child and "
                            "compare the daily series across the restructure date. If a child is "
                            "backfilled across it, switch the slug and re-pull the full history. "
                            "If it only begins at the restructure, declare a series_handover with "
                            "no overlap. Do NOT leave the parent feeding the series either way."))

    def fees(self, project: dict, window_days, out):
        slug, name = project.get("defillama_fees_slug"), project["name"]
        if not slug:
            out.unconfigured(SOURCE, name, "no defillama_fees_slug — not tracked by DefiLlama", TIER)
            return
        if self._absent(name, f"summary/fees/{slug}", out):
            return

        # ===== A CONFIRMED, UNRESOLVED RESTRUCTURE ROUTES fees_usd DIFFERENTLY. Added 2026-09-23.
        # Morpho's `morpho` slug kept answering 200 after DefiLlama split it into a parent with
        # two children — it just stopped being the protocol's fees. See defillama_restructure and
        # _fees_with_restructure_guard for what changes: the parent's post-break residual is
        # never stored, every run re-checks the child that matters, and the fix applies itself
        # the day that child reports again. revenue_usd and holders_revenue_usd are NOT affected
        # by this — Morpho's revenue is hardcoded to 0 by protocol design on both the parent and
        # the child adapters, unrelated to which listing carries the fees — so they keep the
        # ordinary path below.
        restructure = project.get("defillama_restructure")
        if restructure and restructure.get("status") == "confirmed_gap":
            self._fees_with_restructure_guard(project, slug, restructure, window_days, out)
        else:
            try:
                rows = self._summary_chart(slug, "dailyFees")
                out.add(window(tidy(rows, name, "fees_usd", SOURCE, TIER), window_days), SOURCE,
                        name, f"{slug}:dailyFees", TIER)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"{slug}:dailyFees: {e}", TIER)

        for data_type, metric in (("dailyRevenue", "revenue_usd"),
                                  ("dailyHoldersRevenue", "holders_revenue_usd")):
            try:
                rows = self._summary_chart(slug, data_type)
                out.add(window(tidy(rows, name, metric, SOURCE, TIER), window_days), SOURCE, name,
                        f"{slug}:{data_type}", TIER)
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(SOURCE, name, f"{slug}:{data_type}: {e}", TIER)

    def _fees_with_restructure_guard(self, project: dict, slug: str, restructure: dict,
                                     window_days, out) -> None:
        """fees_usd for a project whose DefiLlama listing has a CONFIRMED, unresolved restructure.

        THREE THINGS HAPPEN, every run, and none of them need a human:

        (1) THE PARENT'S POST-BREAK RESIDUAL IS NEVER STORED. Morpho's parent slug answers 200
            and returns a daily chart after the break — it is morpho-midnight's fees wearing
            Morpho's name, to the cent. Storing it would put a wrong-but-plausible number in a
            column a daily check no longer distinguishes from a real one. Only the PRE-BREAK
            history, which is genuinely the protocol's fees, is kept.

        (2) THE WATCH CHILD IS RE-CHECKED. Every run reads its chart and logs whether it has
            reported anything on or after the break date, so a human reading the Run Log sees the
            live state without re-running the probe by hand.

        (3) RECOVERY APPLIES ITSELF. The day the watch child reports again, fees_usd switches to
            the sum of the declared children, the FULL history is re-pulled (never window_days —
            a trimmed re-pull would rewrite only the recent month and leave everything before the
            switch at the old, broken value), and it is stored — replacing every prior fees_usd
            row via the ordinary upsert. Nothing here is a slug swapped on a guess: it is exactly
            the sum(blue, midnight) the probe evidence already showed reconstructs the series
            (pre-break parent tracked blue to within a few dollars a day, so the join is
            continuous), applied the moment the evidence for it exists rather than on suspicion.

        THE LEVEL-BREAK FLAG NEEDS NO EXPLICIT CLEAR. It is computed fresh from the stored
        numbers on every run (fetch.validate.check_level_breaks), so once recovery has stored a
        real series again, the flag simply stops firing — there is no separate acknowledgement
        step to remember.
        """
        name = project["name"]
        watch = restructure["watch_child"]
        break_date = pd.Timestamp(restructure["break_date"])

        try:
            watch_chart = self._chart(self._summary(watch, "dailyFees"))
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{watch}: restructure recovery check failed: {e}", TIER)
            watch_chart = None

        recovered_from = None
        if watch_chart is not None:
            after = [d for d, v in watch_chart if pd.Timestamp(d.date()) >= break_date]
            if after:
                recovered_from = min(after).date().isoformat()
        log.info("%s: restructure recovery check on %s — %s", name, watch,
                 f"RECOVERED, reporting again from {recovered_from}" if recovered_from
                 else f"still no data on or after {break_date.date()}")

        if recovered_from is None:
            try:
                full = self._chart(self._summary(slug, "dailyFees"))
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"{slug}:dailyFees: {e}", TIER)
                return
            pre_break = [(d, v) for d, v in full if pd.Timestamp(d.date()) < break_date]
            if pre_break:
                out.add(window(tidy(pre_break, name, "fees_usd", SOURCE, TIER), window_days),
                        SOURCE, name, f"{slug}:dailyFees (pre-break history only)", TIER)
            out.gap(name, "fees_usd", reason=restructure["gap_reason"], tiers_attempted="1",
                    suggestion=f"Re-checked automatically every run against {watch}'s own "
                              f"listing — no manual probe needed unless this persists for "
                              f"months, at which point the DefiLlama listing itself, not just "
                              f"this project's slug, should be re-examined.")
            return

        # RECOVERED. Sum the declared children over their FULL history and store it — replacing
        # every stored fees_usd row for this project via the ordinary (date, project, metric)
        # upsert, so the series is continuous rather than having a gap at the switch.
        sum_slugs = restructure["recovery"]["sum_slugs"]
        by_date: dict = {}
        ok = True
        for kid in sum_slugs:
            try:
                for d, v in self._chart(self._summary(kid, "dailyFees")):
                    day = pd.Timestamp(d.date())
                    by_date[day] = by_date.get(day, 0.0) + float(v)
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"{kid}: recovery re-pull failed: {e}", TIER)
                ok = False
        if not ok or not by_date:
            return
        rows = sorted(by_date.items())
        out.add(tidy(rows, name, "fees_usd", SOURCE, TIER), SOURCE, name,
                f"RECOVERED — {' + '.join(sum_slugs)}, full history re-pulled, {len(rows)} "
                f"day(s)", TIER)
        out.review_item(
            name, "fees_usd", "source_restructure_recovered", "stored_flagged",
            value=rows[-1][1], date=rows[-1][0].date().isoformat(),
            basis=(f"{slug!r}'s restructure has RECOVERED: {watch} is reporting again from "
                  f"{recovered_from}. fees_usd is now sum({', '.join(sum_slugs)}), full "
                  f"history re-pulled so the series has no gap at the switch. No human step "
                  f"was needed for the switch, the re-pull, or clearing the level-break flag — "
                  f"that flag is computed from the stored numbers on every run, so a correct "
                  f"series simply stops tripping it."),
            source=f"{SOURCE}:recovered", tier=TIER)

    # ------------------------------------------------------------------ TVL
    def protocol_tvl(self, project: dict, window_days, out):
        slug, name = project.get("defillama_protocol"), project["name"]
        if not slug:
            return
        try:
            j = self.http.get(f"{API}/protocol/{slug}")
            rows = [(datetime.fromtimestamp(p["date"], tz=timezone.utc), p["totalLiquidityUSD"]) for p in j.get("tvl", [])]
            out.add(window(tidy(rows, name, "protocol_tvl_usd", SOURCE, TIER), window_days), SOURCE, name, f"{slug}:tvl", TIER)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{slug}:protocol tvl: {e}", TIER)

    def chain_tvl(self, project: dict, window_days, out):
        chain, name = project.get("defillama_chain"), project["name"]
        if not chain:
            return
        try:
            j = self.http.get(f"{API}/v2/historicalChainTvl/{chain}")
            rows = [(datetime.fromtimestamp(p["date"], tz=timezone.utc), p["tvl"]) for p in j]
            out.add(window(tidy(rows, name, "tvl_usd", SOURCE, TIER), window_days), SOURCE, name, f"{chain}:chain tvl", TIER)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{chain}:chain tvl: {e}", TIER)

    def stablecoins(self, project: dict, window_days, out):
        chain, name = project.get("defillama_chain"), project["name"]
        if not chain:
            return
        try:
            j = self.http.get(f"{STABLES}/stablecoincharts/{chain}")
            rows = []
            for p in j:
                circ = p.get("totalCirculatingUSD") or {}
                rows.append((datetime.fromtimestamp(int(p["date"]), tz=timezone.utc),
                             sum(v for v in circ.values() if v)))
            out.add(window(tidy(rows, name, "stablecoin_supply_usd", SOURCE, TIER), window_days), SOURCE, name,
                    f"{chain}:stablecoins", TIER)
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, name, f"{chain}:stablecoins: {e}", TIER)

    # ------------------------------------------------------------------ RWA category by chain
    def _build_rwa_by_chain(self, wanted: set[str]) -> pd.DataFrame:
        protocols = self.http.get(f"{API}/protocols")
        rwa = [p for p in protocols if str(p.get("category", "")).lower() in ("rwa", "rwa lending")]
        frames = []
        for p in rwa:
            if not (set(p.get("chains") or []) & wanted):
                continue
            try:
                j = self.http.get(f"{API}/protocol/{p['slug']}")
            except Exception as e:  # noqa: BLE001
                log.warning("RWA protocol %s failed: %s", p["slug"], e)
                continue
            for chain, block in (j.get("chainTvls") or {}).items():
                if chain not in wanted:
                    continue
                pts = block.get("tvl") or []
                if not pts:
                    continue
                df = pd.DataFrame(pts)
                df["date"] = pd.to_datetime(df["date"], unit="s").dt.normalize()
                df = df.rename(columns={"totalLiquidityUSD": "value"})[["date", "value"]]
                df["chain"] = chain
                frames.append(df)
        if not frames:
            return pd.DataFrame(columns=["date", "chain", "value"])
        return pd.concat(frames).groupby(["chain", "date"], as_index=False)["value"].sum()

    def rwa(self, projects: list[dict], window_days, out):
        wanted = {p["defillama_chain"]: p["name"] for p in projects
                  if p.get("defillama_chain") and 1 in p["archetypes"]}
        if not wanted:
            return
        try:
            if self._rwa_by_chain is None:
                self._rwa_by_chain = self._build_rwa_by_chain(set(wanted))
        except Exception as e:  # noqa: BLE001
            out.fail(SOURCE, None, f"RWA category: {e}", TIER)
            return
        for chain, name in wanted.items():
            sub = self._rwa_by_chain[self._rwa_by_chain["chain"] == chain]
            if sub.empty:
                out.add(None, SOURCE, name, f"{chain}: no RWA-category protocols on chain", TIER)
                continue
            out.add(window(tidy(zip(sub["date"], sub["value"]), name, "rwa_defillama_usd", SOURCE, TIER), window_days),
                    SOURCE, name, f"{chain}:rwa category", TIER)

    def run(self, projects: list[dict], window_days, out):
        for p in projects:
            self.fees(p, window_days, out)
            # AFTER the fetch, so the check costs nothing when the fetch already failed — and
            # runs on every project with a slug rather than only where somebody suspects one.
            # Morpho's restructure sat unnoticed for eleven days precisely because nothing looked
            # unless a human went looking.
            self.check_restructure(p, out)
            self.protocol_tvl(p, window_days, out)
            self.chain_tvl(p, window_days, out)
            self.stablecoins(p, window_days, out)
        self.rwa(projects, window_days, out)
