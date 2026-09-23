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

from .base import Http, point, tidy, today, window

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

    # ===== A CHILD ENTRY IS NOT A SLUG. Fixed 2026-09-22 after it cost 11 run-log failures. =====
    #
    # `childProtocols` is a list of OBJECTS carrying `name` and `defillamaId` — not the strings
    # the first version assumed. `str(k)` on one of them yields the dict's repr, which then went
    # into the URL verbatim: GET /summary/fees/{'name': 'Chainlink Requests', ...} -> HTTP 404,
    # for every child of every parent, on every run.
    #
    # DefiLlama's own slug for a listing is its name lowercased with spaces hyphenated, which is
    # how "Morpho Blue" is morpho-blue and "ether.fi Stake" is ether.fi-stake — note that the dot
    # is kept, because the slug is not sanitised beyond the space. The rule is APPLIED, NOT
    # TRUSTED: a derived slug that does not resolve is logged and the child dropped (see the
    # caller), and the totals_agree test means a slug that resolved to the WRONG listing cannot
    # produce a false restructure report — its 30-day figure simply will not sum to the parent's.
    # `slug` is preferred whenever the payload carries one, so the derivation is the fallback and
    # not the mechanism.
    @staticmethod
    def _child_slug(entry) -> str | None:
        if isinstance(entry, str):
            return entry.strip() or None
        if not isinstance(entry, dict):
            return None
        for key in ("slug", "name"):
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip().lower().replace(" ", "-")
        return None

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

        kids, unnamed = [], 0
        for entry in (parent.get("childProtocols") or []):
            k = self._child_slug(entry)
            if k:
                kids.append(k)
            else:
                unnamed += 1
        if unnamed:
            log.info("%s: %d child entr(ies) of %s carry no name to resolve a slug from",
                     name, unnamed, slug)
        if not kids:
            return
        # A slug that is ITSELF a child is not a restructure — it is the state after one, and
        # config says so. Recorded rather than warned about.
        if parent.get("parentProtocol"):
            log.info("%s: slug %r is a CHILD of %s", name, slug, parent["parentProtocol"])

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
        #
        # ** IT IS COMPUTED BEFORE ANY CHILD IS CALLED, and that ordering is the second fix here.
        # ** Having children is ORDINARY — most of the sixteen slugs that are parents have nothing
        # wrong with them — and the first version read every child of every parent on every run to
        # reach a conclusion the parent's own listing already settles. That doubled the tier's wall
        # clock (63s -> 125s) and turned every unreadable child into a run-log failure against a
        # healthy protocol. The children are corroboration for a collapse already seen, not a survey.
        parent_30d = float(parent.get("total30d") or 0.0)
        pchart = self._chart(parent)
        if not pchart or parent_30d <= 0:
            return
        newest = pchart[-1][0]
        recent = sum(v for d, v in pchart if (newest - d).days < RESTRUCTURE_RECENT_DAYS)
        implied = parent_30d / 30.0 * RESTRUCTURE_RECENT_DAYS
        if recent * RESTRUCTURE_FACTOR >= implied:
            return
        parent_recent = recent

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
            # SKIPPED, NOT FAILED, and that distinction is the first fix here. A child listing
            # that will not answer is not a failure of this project's fetch — fees_usd has
            # already been read from the parent by fees(). What it does do is make the sum below
            # an UNDER-count, so totals_agree can come out false and the check stay silent on a
            # real restructure. This line puts that incompleteness on the record even when the
            # conclusion cannot be reached.
            out.skipped(SOURCE, name,
                        f"{slug}: restructure check INCOMPLETE — {len(failed)} of {len(kids)} "
                        f"child listing(s) unreadable ({', '.join(failed)}). The children's "
                        f"30-day sum is a lower bound, so the check may stay silent.", TIER)
        if not child_rows:
            return

        # THE CHILDREN ARE WHERE THE MONEY WENT, and the totals agreeing is what proves it did
        # not simply stop. Without this a listing whose chart lags its total would be reported as
        # a restructure.
        totals_agree = abs(parent_30d - child_30d) <= max(parent_30d, child_30d) * 0.01
        if not totals_agree:
            return

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
        watch_days: set = set()
        ok = True
        for kid in sum_slugs:
            # ===== ** SUMMING ACROSS SLUGS IS THE JOB. SUMMING WITHIN ONE IS A BUG. ** =====
            # This accumulated straight into by_date, so two points from the SAME slug on one UTC
            # date merged silently into a number that looks like a day and is not. Summing the
            # children is the whole point of recovery, so the two cases have to be told apart:
            # each slug is read into its own dict, a repeated date there is REPORTED rather than
            # added, and only then is the slug folded into the total.
            #
            # NOT CURRENTLY TRIGGERED — the 2026-09-24 store has no duplicate dates. It is fixed
            # anyway: a latent fault that happened not to fire is not a fixed one, and this
            # branch runs on every future restructure recovery, when a re-indexing provider is
            # exactly the thing most likely to emit a date twice.
            per_slug: dict = {}
            dupes: list = []
            try:
                for d, v in self._chart(self._summary(kid, "dailyFees")):
                    day = pd.Timestamp(d.date())
                    if day in per_slug:
                        dupes.append((day, per_slug[day], float(v)))
                        continue        # KEEP THE FIRST, never the sum — see below
                    per_slug[day] = float(v)
            except Exception as e:  # noqa: BLE001
                out.fail(SOURCE, name, f"{kid}: recovery re-pull failed: {e}", TIER)
                ok = False
                continue
            if dupes:
                # ** REPORTED AND REFUSED, NOT REPAIRED. ** Which of the two points is the day's
                # figure is not knowable from here — the provider may be mid-reindex, or may have
                # split one day across two stamps — and picking one is a guess that ships as a
                # number. The run fails for this project so nothing is stored on a series whose
                # shape is not understood, and the message carries the dates and both values so
                # the next step is a reading rather than another run.
                shown = "; ".join(f"{d.date()}: {a:,.2f} and {b:,.2f}" for d, a, b in dupes[:5])
                out.fail(SOURCE, name,
                         f"{kid}: {len(dupes)} DUPLICATE DATE(S) in the recovery re-pull — "
                         f"{shown}"
                         + (f" (and {len(dupes) - 5} more)" if len(dupes) > 5 else "")
                         + ". NOT summed and NOT stored: two points on one UTC date is a "
                           "provider-shape change, and adding them would put an aggregate in a "
                           "daily column. Read the slug's chart before re-running.", TIER)
                ok = False
                continue
            for day, val in per_slug.items():
                by_date[day] = by_date.get(day, 0.0) + val
                if kid == watch:
                    watch_days.add(day)
        if not ok or not by_date:
            return

        # ===== ** RECOVERY DOES NOT MEAN BACKFILL, AND THE TWO WERE BEING CONFLATED. ** =====
        # This line used to store every day of the summed history and say in the review row that
        # the series "has no gap at the switch". That was ASSERTED, never checked — and for the
        # days between the break and the recovery it is most likely FALSE. If DefiLlama's
        # indexing failure simply ended on the recovery date without filling in what it missed,
        # the watch child has no points for 09-12..09-20, and the sum for those days is the OTHER
        # child alone: Morpho Midnight's ~$2/day standing in for Morpho Blue's ~$600,000/day.
        #
        # ** THAT IS THE EXACT FAILURE THE PRE-RECOVERY BRANCH ABOVE REFUSES **, arriving through
        # the back door. Point (1) of this method's contract is that the parent's post-break
        # residual is never stored because it is a wrong-but-plausible number in a column no
        # daily check distinguishes from a real one. A recovery that writes the same residual
        # under a new label, and declares continuity while doing it, is worse than the gap: the
        # gap was visible.
        #
        # SO THE DAYS THE WATCH CHILD DOES NOT COVER ARE HELD OUT, AND NAMED. An absent day is a
        # hole a reader can see. A $2.13 day is a number they will believe.
        # ===== ** THE WINDOW IS THE WHOLE BREAK, NOT JUST UP TO THE FIRST RECOVERED DAY. ** =====
        # This read `d < pd.Timestamp(recovered_from)`, and recovered_from is the EARLIEST day the
        # watch child reports on or after the break. A PARTIAL backfill therefore shrank the
        # protected window to nothing: if DefiLlama filled 09-15 alone, recovered_from moved from
        # 09-21 back to 09-15, and 09-16..09-20 — still missing — fell OUTSIDE the hold-out and
        # were stored as Morpho Midnight's ~$2/day standing in for Blue's ~$600,000.
        #
        # ** AND THE TRIGGER IS THE EVENT THIS WATCH EXISTS TO WAIT FOR. ** A backfill arriving
        # out of order is not an edge case here; it is the expected shape of the thing being
        # watched for. Verified by driving this function with a stubbed chart: the old form
        # stored five residual rows, this one stores none.
        #
        # The rule is simply the contract stated in point (1) of this method's docstring: a day
        # the WATCH CHILD does not cover is not this protocol's fees, whenever it falls. That
        # deliberately extends past the original break too — if the watch child ever lags a day
        # behind the other child, that day is the other child alone and must not be stored either.
        uncovered = sorted(d for d in by_date if d >= break_date and d not in watch_days)
        rows = sorted((d, v) for d, v in by_date.items() if d not in set(uncovered))
        if not rows:
            return
        held = (f"; {len(uncovered)} day(s) HELD OUT "
                f"({uncovered[0].date()}..{uncovered[-1].date()}) — {watch} has no point on "
                f"them, so the sum there would be the other child alone"
                if uncovered else "")
        out.add(tidy(rows, name, "fees_usd", SOURCE, TIER), SOURCE, name,
                f"RECOVERED — {' + '.join(sum_slugs)}, full history re-pulled, {len(rows)} "
                f"day(s){held}", TIER)
        if uncovered:
            # THE HOLE IS REPORTED AS A GAP IN ITS OWN RIGHT, not folded into the recovery row.
            # A reader scanning the Gap Report for "why does fees_usd stop here" must find this.
            out.gap(name, "fees_usd",
                    reason=(f"{len(uncovered)} day(s) between the break ({break_date.date()}) "
                            f"and the recovery ({recovered_from}) are NOT STORED: {watch} "
                            f"reported nothing on them, so sum({', '.join(sum_slugs)}) would be "
                            f"the other child alone — a small, plausible number standing in for "
                            f"the protocol's fees. Left absent deliberately: a hole is visible "
                            f"and a wrong number is not."),
                    tiers_attempted="1",
                    suggestion=(f"Nothing to do unless DefiLlama backfills {watch} for "
                                f"{uncovered[0].date()}..{uncovered[-1].date()}, which this "
                                f"check picks up on its own the next run. Do NOT fill them by "
                                f"interpolation or by carrying the neighbouring days."))
        out.review_item(
            name, "fees_usd", "source_restructure_recovered", "stored_flagged",
            value=rows[-1][1], date=rows[-1][0].date().isoformat(),
            basis=(f"{slug!r}'s restructure has RECOVERED: {watch} is reporting again from "
                  f"{recovered_from}. fees_usd is now sum({', '.join(sum_slugs)}), full "
                  f"history re-pulled. "
                  + (f"** THE BREAK WINDOW IS NOT BACKFILLED: {len(uncovered)} day(s) "
                     f"({uncovered[0].date()}..{uncovered[-1].date()}) are absent because "
                     f"{watch} has no point on them. The series is continuous either side and "
                     f"the hole is VISIBLE, which is the intended state — storing the other "
                     f"child alone there would have hidden it behind a plausible number. **"
                     if uncovered else
                     f"{watch} covers the whole break window, so the series is continuous "
                     f"across it — checked against its own chart, not assumed.")
                  + f" No human step was needed for the switch, the re-pull, or clearing the "
                    f"level-break flag — that flag is computed from the stored numbers on every "
                    f"run, so a correct series simply stops tripping it."),
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

    # ===== A LENDING PROTOCOL'S SUPPLY SIDE, AND THE CAVEAT THAT TRAVELS WITH IT. =====
    #
    # supply_units and utilisation_pct are archetype 2's two capacity columns, and for a lending
    # protocol they come from the same pair of DefiLlama series:
    #     supply_units    = tvl + borrowed          (total supplied, in USD)
    #     utilisation_pct = borrowed / supply_units
    #
    # ** THE DENOMINATOR CARRIES COLLATERAL, AND THAT IS NOT A DETAIL. ** Read from
    # DefiLlama-Adapters/projects/morpho-blue/index.js on 2026-09-23, the tvl function builds its
    # token list from BOTH loanToken AND collateralToken of every market and sums the Morpho Blue
    # singleton's balance of each. Morpho Blue custodies collateral, so it is in there.
    #
    # Total SUPPLIED is idle loan tokens plus borrowed. tvl + borrowed is idle loan tokens plus
    # COLLATERAL plus borrowed. So this denominator is too large by the collateral, and the
    # utilisation it produces is too SMALL — a known direction, which is the only thing that
    # makes it usable at all. It is stored with that on the label rather than left blank, and
    # config.non_comparable carries the reason to the cell.
    #
    # The exact figure — sum(totalBorrowAssets) / sum(totalSupplyAssets) across markets — is one
    # contract read away and zero aggregator calls away, because the same adapter reads
    # totalSupplyAssets and exports only the borrow side. See utilisation_pct_blocked.
    def lending_supply(self, project: dict, window_days, out):
        spec = project.get("lending_supply") or {}
        slug, name = project.get("defillama_protocol"), project["name"]
        if not spec or not slug:
            return
        # ===== THE UNBIASED ROUTE WINS, AND THE TWO NEVER ALTERNATE. Added 2026-09-23. =====
        # Once the protocol's own API is confirmed, it writes these columns and this route stands
        # down COMPLETELY — not "unless it fails". A run where the API is down must leave the
        # column empty rather than filling it from here, because two sources taking turns is a
        # measuring-point change and blanks the whole series. That is the failure this file has
        # now recorded three times.
        api = project.get("lending_api") or {}
        if api.get("status") == "confirmed":
            out.skipped(SOURCE, name,
                        f"supply_units and utilisation_pct: NOT taken from DefiLlama — "
                        f"{api.get('endpoint')} is confirmed and serves them without the "
                        f"collateral in the denominator. The two routes must not alternate.",
                        TIER)
            return
        try:
            j = self.http.get(f"{API}/protocol/{slug}")
        except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
            out.fail(SOURCE, name, f"{slug}:lending supply: {e}", TIER)
            return

        chain_tvls = j.get("chainTvls") or {}
        borrowed = (chain_tvls.get(spec.get("borrowed_key", "borrowed")) or {}).get("tvl")
        if not isinstance(borrowed, list) or not borrowed:
            # THE KEYS THE RESPONSE DID CARRY ARE NAMED, so the next run's log says what shape
            # arrived rather than only that the expected one did not.
            out.fail(SOURCE, name,
                     f"{slug}: no {spec.get('borrowed_key', 'borrowed')!r} series in chainTvls. "
                     f"Keys present: {', '.join(sorted(chain_tvls)[:12]) or 'none'}", TIER)
            out.gap(name, "utilisation_pct",
                    reason=(f"DefiLlama's /protocol/{slug} carries no "
                            f"{spec.get('borrowed_key', 'borrowed')!r} series, so there is no "
                            f"borrow side to divide by. Keys present: "
                            f"{', '.join(sorted(chain_tvls)[:12]) or 'none'}."),
                    tiers_attempted="1",
                    suggestion=("Check the key name against the live response before changing "
                                "anything — this is read from chainTvls, not from the headline "
                                "tvl series."))
            return

        supplied_side = {int(r["date"]): float(r["totalLiquidityUSD"])
                         for r in (j.get("tvl") or []) if r.get("totalLiquidityUSD") is not None}
        rows_units, rows_util = [], []
        for r in borrowed:
            ts = int(r["date"])
            b = float(r.get("totalLiquidityUSD") or 0.0)
            tvl = supplied_side.get(ts)
            if tvl is None:
                continue
            total = tvl + b
            if total <= 0:
                continue
            when = datetime.fromtimestamp(ts, tz=timezone.utc)
            rows_units.append((when, total))
            rows_util.append((when, b / total))

        if not rows_units:
            out.fail(SOURCE, name,
                     f"{slug}: the borrowed series and the tvl series share no dates, so neither "
                     f"figure can be formed. Nothing was stored rather than pairing them by "
                     f"position, which would align two series on their INDEX and not their DATE.",
                     TIER)
            return
        for metric, rows in (("supply_units", rows_units), ("utilisation_pct", rows_util)):
            out.add(window(tidy(rows, name, metric, SOURCE, TIER), window_days), SOURCE, name,
                    f"{slug}:{metric} = " + ("tvl + borrowed" if metric == "supply_units"
                                             else "borrowed / (tvl + borrowed)")
                    + " — DENOMINATOR INCLUDES COLLATERAL, see non_comparable", TIER)

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
            self.lending_supply(p, window_days, out)
            self.chain_tvl(p, window_days, out)
            self.stablecoins(p, window_days, out)
        self.rwa(projects, window_days, out)


# ===== MORPHO'S OWN API: THE SAME FIGURES THE TVL ADAPTER READS AND DISCARDS. 2026-09-23. =====
#
# ** THE DEFILLAMA ROUTE CAN ONLY APPROXIMATE THIS. ** morpho-blue's tvl function sums the Morpho
# Blue singleton's balance of every market's collateralToken as well as its loanToken, so
# tvl + borrowed is supplied PLUS collateral and the utilisation it yields reads too small.
#
# Morpho publishes the per-market state — supplyAssetsUsd and borrowAssetsUsd — through a free
# GraphQL endpoint. Summed across markets that is utilisation with no collateral in the
# denominator: the figure itself rather than a labelled approximation of it.
#
# ** IT WRITES NOTHING WHILE status IS unconfirmed. ** The endpoint is egress-blocked from the
# environment this was written in, so it has not been shown to answer. A source is not promoted
# to primary until it fetches on a live run — this book has been burned by the other order. The
# adapter runs, reports exactly what came back, and refuses to store until somebody flips the
# flag on the strength of a run that worked.
class MorphoBlueApi:
    """Per-market supply and borrow, summed, from Morpho's own GraphQL API."""

    SOURCE = "morpho_api"
    TIER = 1

    def __init__(self, **_ignored):
        self.http = Http(min_interval=0.5)

    def _query(self, endpoint: str, query: str, variables: dict | None = None):
        return self.http.post(endpoint, json_body={"query": query,
                                                   "variables": variables or {}})

    def run(self, projects: list[dict], window_days, out):
        when = today()
        for p in projects:
            api = p.get("lending_api") or {}
            if not api.get("endpoint"):
                continue
            name = p["name"]
            try:
                chains = self._query(api["endpoint"], api["chains_query"])
                ids = [c["id"] for c in (((chains or {}).get("data") or {}).get("chains") or [])]
            except Exception as e:  # noqa: BLE001 — a failed source must not kill the run
                out.fail(self.SOURCE, name, f"{api['endpoint']}: chains query failed: {e}",
                         self.TIER)
                continue
            if not ids:
                out.fail(self.SOURCE, name,
                         f"{api['endpoint']} answered but listed no chains. Response keys: "
                         f"{', '.join(sorted(chains or {})) or 'none'}", self.TIER)
                continue

            supply, borrow, seen, skip = 0.0, 0.0, 0, 0
            empty, lopsided, unfiltered = 0, [], 0
            verify = api.get("verify_field")
            try:
                while True:
                    page = self._query(api["endpoint"], api["markets_query"],
                                       {"c": ids, "skip": skip,
                                        "first": int(api.get("page_size", 1000))})
                    node = (((page or {}).get("data") or {}).get("markets") or {})
                    items = node.get("items") or []
                    for it in items:
                        seen += 1
                        # ===== ** THE FILTER IS VERIFIED, NOT TRUSTED. ** A where-clause the
                        # server does not understand is not an error — it comes back 200 with
                        # the WHOLE permissionless population, which is $39.47bn of mostly
                        # fabricated supply at 98% utilisation. Asking each row to confirm it is
                        # listed is the only thing standing between a dropped filter and that
                        # number being written to the sheet.
                        if verify and it.get(verify) is not True:
                            unfiltered += 1
                        st = it.get("state") or {}
                        sv, bv = st.get(api["supply_field"]), st.get(api["borrow_field"])
                        # ===== TWO KINDS OF ABSENCE, AND ONLY ONE OF THEM IS A PROBLEM. =====
                        # ** BOTH FIELDS ABSENT: an empty market. ** It contributes nothing to
                        # either side, so dropping it and summing it as zero are the same
                        # arithmetic — the difference is only whether anyone is alarmed. The
                        # 2026-09-23 probe settled this on the real population: of 1,117 markets
                        # with no borrowAssetsUsd, ZERO carried a supply figure.
                        #
                        # ** ONE PRESENT AND THE OTHER ABSENT: refuse the whole read. ** That is
                        # a market with supply and an unreadable borrow side, and there is no
                        # safe default — summing the missing side as 0 understates utilisation,
                        # and dropping the market understates supply. It has not been observed,
                        # and the guard stays for the day it is.
                        if sv is None and bv is None:
                            empty += 1
                            continue
                        if sv is None or bv is None:
                            lopsided.append(str(it.get("marketId"))[:18])
                            continue
                        supply += float(sv)
                        borrow += float(bv)
                    total = (node.get("pageInfo") or {}).get("countTotal")
                    if not items or (total is not None and seen >= int(total)):
                        break
                    skip += int(api.get("page_size", 1000))
            except Exception as e:  # noqa: BLE001
                out.fail(self.SOURCE, name, f"{api['endpoint']}: markets query failed: {e}",
                         self.TIER)
                continue

            detail = (f"{seen} listed market(s) across {len(ids)} chain(s): "
                      f"{api['supply_field']}={supply:,.0f}, {api['borrow_field']}={borrow:,.0f}"
                      + (f"; {empty} empty market(s) carried neither field and were dropped"
                         if empty else ""))
            if unfiltered:
                out.fail(self.SOURCE, name,
                         f"{api['endpoint']}: THE LISTED FILTER DID NOT APPLY — {unfiltered} "
                         f"of {seen} market(s) came back with {verify} not true, although the "
                         f"query asks for where:{{{api.get('filter_field')}:true}}. A filter the "
                         f"server ignores returns the WHOLE permissionless population, which is "
                         f"~$39.5bn at ~98% utilisation and mostly fabricated oracle prices. "
                         f"NOTHING STORED. Re-check the filter's field name against the schema "
                         f"before changing anything here.", self.TIER)
                continue
            if lopsided:
                # ** ONE SIDE PRESENT WITHOUT THE OTHER. ** Not observed on the real population,
                # and kept because the day it happens there is no safe default — see above.
                out.fail(self.SOURCE, name,
                         f"{api['endpoint']}: {len(lopsided)} market(s) carry ONE of "
                         f"{api['supply_field']}/{api['borrow_field']} and not the other "
                         f"({', '.join(lopsided[:5])}{'...' if len(lopsided) > 5 else ''}). "
                         f"{detail}. NOTHING SUMMED INTO A FIGURE — summing the missing side as "
                         f"0 understates utilisation and dropping the market understates supply, "
                         f"and which is right is not knowable from the absence itself.",
                         self.TIER)
                continue
            if supply <= 0:
                out.fail(self.SOURCE, name, f"{api['endpoint']}: supply summed to 0. {detail}",
                         self.TIER)
                continue

            if api.get("status") != "confirmed":
                # THE WHOLE POINT OF THE UNCONFIRMED STATE: it reports what it would have
                # written, so one run settles whether the route works — and writes nothing.
                #
                # ===== ** "IT WORKED" IS WHAT THIS LINE USED TO SAY, AND IT WAS WRONG. ** =====
                # The first live run fetched cleanly and reported $39.47bn supplied against
                # $38.73bn borrowed — 98.1% utilisation, which no lending protocol runs at. The
                # message called that a success and invited a human to flip the flag, because
                # the only thing it had actually established was that the FIELD NAMES were
                # right. A route is confirmed when its number is EXPLAINED, not when it parses.
                # So the line now says what it really showed, and when the implied utilisation
                # is outside the band it says plainly that this is not ready to confirm.
                band = api.get("plausible_utilisation")
                util = borrow / supply
                bad = bool(band) and not (float(band[0]) <= util <= float(band[1]))
                out.skipped(self.SOURCE, name,
                            f"supply_units and utilisation_pct NOT stored — lending_api.status "
                            f"is {api.get('status')!r}. THE FETCH PARSED: {detail}, which would "
                            f"give supply_units={supply:,.0f} and utilisation_pct={util:.4f}."
                            + (f" *** DO NOT CONFIRM ON THIS. *** {util:.4f} is outside the "
                               f"plausible {float(band[0]):.2f}-{float(band[1]):.2f} band for an "
                               f"aggregate lending utilisation, so the sum does not mean what "
                               f"the field name says and the route would swap a labelled bias "
                               f"for an unlabelled one. Run check_offline_items.py "
                               f"morpho_blue_api for the listed/unlisted split and the "
                               f"DefiLlama TVL cross-check before touching status."
                               if bad else
                               f" Parsing is NOT the bar: confirm only once the total has been "
                               f"cross-checked against DefiLlama's TVL for the same protocol. "
                               f"Then status 'confirmed' takes this route and stands "
                               f"DefiLlama's down."),
                            self.TIER)
                continue

            for metric, value in (("supply_units", supply),
                                  ("utilisation_pct", borrow / supply)):
                out.add(point(name, metric, value, f"{self.SOURCE}:markets", self.TIER, when),
                        self.SOURCE, name,
                        f"{metric} from Morpho's own API — {detail}. NO COLLATERAL in the "
                        f"denominator, unlike the DefiLlama route this replaces.", self.TIER)
