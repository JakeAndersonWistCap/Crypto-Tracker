#!/usr/bin/env python3
"""Regenerate tests/fixtures/stale_store.json — the stale-store regression snapshot.

WHY THIS FIXTURE EXISTS
-----------------------
Three "tested and fixed" claims failed on a live run in one session — Maple's disputed
destination, Ether.fi's kind split, GEODNET's suppression gate. All three passed their unit
tests. All three were WRITE-PATH tests: they proved the adapter would stop producing the bad
value, and said nothing about the bad value already sitting in the store.

The store upserts and never deletes, so every config change has two halves. Only one was ever
being tested.

This fixture is rows written under a PRE-FIX config. The tests assert those rows read correctly
NOW. It is the half that was missing.

WHAT IT COVERS — the six known ways a stored row's basis can change out from under it:
    removed_contract        the contract key is gone from config
    repurposed_contract     the key is still there, its KIND changed (Ether.fi sethfi)
    suppressed_derivation   config switched the derivation off (GEODNET issuance)
    disputed_destination    the contract's ROLE is in doubt (Maple treasury)
    refuted_mechanism       the project does not burn the way this metric measures (Sky)
    changed_measuring_point one series read from two different places over its history

Plus NEGATIVE CONTROLS, which are not decoration: a guard that blanks everything passes every
positive assertion in this file. The controls are what prove the guards are targeted.

HOW TO REFRESH
--------------
    python tests/refresh_stale_fixture.py

Row DEFINITIONS live here, in code, where they can be reviewed. EXPECTATIONS are computed by
running the current build_workbook and written to the JSON. So refreshing after a deliberate
behaviour change is one command — and refreshing after an ACCIDENTAL one silently blesses the
regression, which is why the JSON is committed and reviewed in the diff like any other change.

Never refresh to make a failing test pass. Read the diff first: if an expectation moved and you
did not intend it, that is the bug the fixture exists to catch.
"""
from __future__ import annotations

import contextlib
import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import build_workbook as bw  # noqa: E402
import config  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent / "fixtures" / "stale_store.json"
ASOF = "2026-09-15"


@contextlib.contextmanager
def forced_dispute():
    """Hold Maple's treasury contract disputed for the duration of the block.

    NO CONTRACT IN LIVE CONFIG IS DISPUTED ANY MORE, so without this the disputed branch has no
    example and the fixture stops covering it. Maple's was the original one and it resolved on
    2026-09-18 (new address, verified_by_label). The mechanism did not go away with it, so the
    example is kept as an explicit counterfactual rather than deleted.

    ** WHY THIS IS A SHARED HELPER AND NOT AN INLINE TWO-LINER. ** It was inline in the test,
    and on 2026-09-21 it silently stopped working: the cross-check demotion gave the treasury
    contract a metric_override, so destination_disputed() — which resolves contract -> metric
    through that override — no longer matched treasury_holding_tokens whatever the flag said.
    Setting the flag kept "working" while testing nothing. Every consumer now enters the same
    counterfactual through one door, and the row below names the override's metric, so the two
    halves cannot drift apart again without the coverage assertion catching it.
    """
    treasury = config.PROJECT_BY_NAME["Maple"]["contracts"]["treasury"]
    previous = treasury.get("destination_status")
    treasury["destination_status"] = "disputed"
    try:
        yield
    finally:
        treasury["destination_status"] = previous

# Every transition type the fixture must cover. The coverage test asserts this set is fully
# exercised AND that confidence_for has not grown a RED branch nobody added a row for.
TRANSITIONS = (
    "removed_contract",
    "repurposed_contract",
    "suppressed_derivation",
    "disputed_destination",
    "refuted_mechanism",
    "changed_measuring_point",
    "implausible_delta",
    "control",
)

# ROW DEFINITIONS. Each carries the config shape it was written under and why it is here.
# Values are real ones off live runs wherever possible — a fixture of round numbers hides
# scaling and precision bugs that a real figure would surface.
ROWS = [
    # ---- removed_contract ------------------------------------------------------------
    # Sky's burn_zero rows outlived the contract's removal by days and showed as a measured
    # zero on a refuted mechanism, at full confidence. The original case.
    dict(transition="removed_contract", date="2026-09-10", project="Sky",
         metric="burn_address_balance", value=0.0, source="chain:ethereum:burn_zero", tier=2,
         written_under="config when Sky had a burn_zero contract",
         why="the key is gone from config, so the row measures something this tool decided "
             "not to measure"),
    dict(transition="removed_contract", date="2026-09-11", project="Sky",
         metric="burn_address_balance", value=0.0, source="chain:ethereum:burn_zero", tier=2,
         written_under="config when Sky had a burn_zero contract",
         why="second day of the same orphaned series — n_points > 1 must not soften it"),

    # ---- repurposed_contract ---------------------------------------------------------
    # THE ONE THE ORPHAN GUARD MISSED. sethfi is still in config; its kind moved from
    # ve_total_supply to stake_underlying, so it no longer feeds locked_tokens at all.
    dict(transition="repurposed_contract", date="2026-09-13", project="Ether.fi",
         metric="locked_tokens", value=111_163_214.703019,
         source="chain:ethereum:sethfi:PARTIAL", tier=2,
         written_under="config when sethfi had kind ve_total_supply",
         why="ASSETS sitting in the SHARES column. The key still exists, so the orphan guard "
             "passes it — this is the case that needed a second guard"),
    dict(transition="repurposed_contract", date="2026-09-14", project="Ether.fi",
         metric="locked_tokens", value=111_163_214.703019,
         source="chain:ethereum:sethfi:PARTIAL", tier=2,
         written_under="config when sethfi had kind ve_total_supply",
         why="the newest row, which is the one the workbook shows"),

    # ---- suppressed_derivation -------------------------------------------------------
    # The derived tier made 0 OK calls on run 20260915T120711Z, yet this rendered ok.
    dict(transition="suppressed_derivation", date="2026-09-13", project="GEODNET",
         metric="gross_issuance_tokens", value=0.0,
         source="derived:d_supply:MECHANISM_ASSUMED", tier=2,
         written_under="config before issuance_derivation.suppressed was set",
         why="a false zero, which is worse than a gap: it renders as measured and feeds the "
             "burn/issuance ratio as a denominator"),
    dict(transition="suppressed_derivation", date="2026-09-14", project="GEODNET",
         metric="gross_issuance_tokens", value=0.0,
         source="derived:d_supply:MECHANISM_ASSUMED", tier=2,
         written_under="config before issuance_derivation.suppressed was set",
         why="the newest row"),

    # ---- disputed_destination --------------------------------------------------------
    # 0.51 SYRUP against ~75.78m reported. The read was right; the ADDRESS was wrong.
    # THE METRIC IS THE OVERRIDE TARGET, not treasury_holding_tokens. Since the chain read was
    # demoted to a cross-check (2026-09-18) the treasury contract serves
    # treasury_holding_tokens_chain_crosscheck, and a row on the old metric is caught by the
    # WITHDRAWN branch instead — which is what quietly cost this branch its coverage. Naming the
    # metric the contract actually serves is what makes the row test disputed and nothing else.
    dict(transition="disputed_destination", date="2026-09-14", project="Maple",
         metric="treasury_holding_tokens_chain_crosscheck", value=0.5125357033239131,
         source="chain:ethereum:treasury", tier=2,
         written_under="config before destination_status='disputed' was set on treasury",
         why="the first of these bugs found. Suppressing the WRITE could not touch this row"),

    # ---- refuted_mechanism -----------------------------------------------------------
    # Sky swaps on an AMM; no dead address is involved. A non-contract source is used on
    # purpose so the orphan and withdrawn guards do NOT fire and refuted is what is tested.
    dict(transition="refuted_mechanism", date="2026-09-14", project="Sky",
         metric="gross_burn_tokens", value=1_234_567.0, source="dune:9999001", tier=4,
         written_under="config before burn_mechanism.status='refuted'",
         why="a burn figure on a project positively established not to burn this way. Sourced "
             "from Dune so neither contract guard can claim it first"),

    # ---- changed_measuring_point -----------------------------------------------------
    # Two sources for one metric across the history: a window spanning the change reports the
    # move between two different addresses as though it were a flow.
    dict(transition="changed_measuring_point", date="2026-09-12", project="Chainlink",
         metric="buyback_fund_balance", value=5_200_000.0,
         source="chain:ethereum:reserve_old", tier=2,
         written_under="config when the Reserve was read at a different address",
         why="the superseded measuring point"),
    dict(transition="changed_measuring_point", date="2026-09-14", project="Chainlink",
         metric="buyback_fund_balance", value=5_400_000.0,
         source="chain:ethereum:reserve", tier=2,
         written_under="current config",
         why="the current measuring point. Together these two make measuring_points > 1"),

    # ---- implausible_delta -----------------------------------------------------------
    # THE ONE BOTH OTHER GUARDS MISSED, found by the audit of run 20260921T100546Z a week
    # after they were built. Uniswap's burn read moved from fire_pit to the dead address on
    # 2026-09-14 and the delta across the change stored 111,337,581 UNI as ONE DAY'S BURN —
    # 99.99% of every UNI ever burned, against a real rate of 100-200k/day.
    #
    # Neither existing guard could see it. The write-time one (derive_flow_from_cumulative)
    # was added AFTER this row and cannot reach backwards. changed_measuring_point needs two
    # distinct sources in the history — and the orphan cleanup had deleted the fire_pit rows,
    # which removed the EVIDENCE and left the CONSEQUENCE, so only one source remained.
    #
    # Note the source below carries the NEW address and the :delta marker. That is the whole
    # point: it looks like an ordinary differenced flow and is only detectable as arithmetic.
    # ON VENICE, NOT UNISWAP, although Uniswap is where it actually happened: Uniswap already
    # holds a CONTROL row on this exact metric (an ordinary 134,000 burn flow), and an artefact
    # in the same series would correctly blank that control and destroy what it is there to
    # prove. Venice is the same shape — a transfer burn with a dead-address contract — and has
    # no other row in the fixture to collide with.
    dict(transition="implausible_delta", date="2026-09-14", project="Venice AI",
         metric="gross_burn_tokens", value=111_337_581.0,
         source="chain:base:burn_zero:delta", tier=2,
         written_under="config before the burn read moved address, differenced across the move",
         why="one day's burn at 99% of the cumulative. Carries a SINGLE source, so "
             "changed_measuring_point cannot catch it — only the ratio to the stock can"),
    # LABELLED control, NOT implausible_delta, although it sits in this section and the guard
    # cannot fire without it. It is the DENOMINATOR: the cumulative the bogus flow above is
    # measured against. The stock itself is a perfectly good reading — 111,953,581 really is
    # the dead address's balance — and must keep showing its number. Calling it a control is
    # what asserts that: a guard keyed on the project, or on the parent metric, rather than on
    # the flow alone would blank this too, and the generative walk would catch it. Filed under
    # implausible_delta it would instead be REQUIRED to go RED, which is the opposite of true.
    dict(transition="control", date="2026-09-15", project="Venice AI",
         metric="burn_address_balance", value=111_953_581.0,
         source="chain:base:burn_zero", tier=2,
         written_under="current config",
         why="the cumulative the flow above is measured against. Without it there is nothing "
             "to take a ratio of and the guard correctly stays silent — and the stock must "
             "survive the guard that blanks the flow derived from it"),

    # ---- controls --------------------------------------------------------------------
    # A GUARD THAT BLANKS EVERYTHING PASSES EVERY ASSERTION ABOVE. These must stay ok.
    dict(transition="control", date="2026-09-14", project="Ether.fi",
         metric="locked_tokens_underlying", value=111_163_214.703019,
         source="chain:ethereum:sethfi:PARTIAL", tier=2,
         written_under="current config",
         why="THE SAME CONTRACT feeding its NEW metric. The guard is about the PAIRING, not "
             "about the contract — if this blanks, the guard is too broad"),
    # ** NOT A CONTROL, AND THE GENERATIVE TEST IS WHY. ** This row was written as one — "the
    # Dune history must survive the guard" — and it does not, because it shares a
    # (project, metric) key with the contaminated sethfi rows above. aggregate() groups by that
    # key and takes the LATEST row's source, which is sethfi, so the WHOLE series blanks.
    #
    # That is the correct call and a real consequence worth stating: while locked_tokens holds
    # both a legitimate Dune history and contaminating contract rows, the column is not
    # trustworthy and shows nothing. The 794 days come back the moment the re-attribution in
    # orphan_cleanup.sql runs — they are not lost, they are withheld.
    #
    # Filed under repurposed_contract rather than control so the fixture states what actually
    # happens. Calling it a control and asserting it stays visible would have been a test
    # written to match a belief instead of the behaviour.
    dict(transition="repurposed_contract", date="2026-09-10", project="Ether.fi",
         metric="locked_tokens", value=141_470_107.5, source="dune:8683038", tier=4,
         written_under="current config — but sharing a metric with rows that are not",
         why="A CLEAN ROW IN A CONTAMINATED SERIES. It blanks with the rest, because the series "
             "is keyed on (project, metric) and the newest source is the withdrawn one. "
             "Recovered by the cleanup, not by a code change"),
    dict(transition="control", date="2026-09-14", project="Uniswap",
         metric="gross_burn_tokens", value=134_000.0,
         source="chain:ethereum:burn_dead:delta", tier=2,
         written_under="current config",
         why="A DERIVED FLOW, whose source names the contract whose BALANCE was differenced. "
             "burn_dead's kind is burn_address_balance, NOT gross_burn_tokens — a naive "
             "'does this contract serve this metric' test turns every burn flow in the book red"),
    dict(transition="control", date="2026-09-14", project="PancakeSwap",
         metric="burn_address_balance", value=4_991_087_156.70303,
         source="chain:bsc:burn_dead", tier=2,
         written_under="current config",
         why="an ordinary contract read on an ordinary metric"),
    dict(transition="control", date="2026-09-14", project="Maple",
         metric="total_supply", value=1_190_000_000.0, source="chain:ethereum:token", tier=2,
         written_under="current config",
         why="an UNDISPUTED metric on the project that has a disputed one. Suppression must be "
             "per-metric, not per-project"),
    dict(transition="control", date="2026-09-14", project="Sky",
         metric="total_supply", value=22_000_000_000.0, source="chain:ethereum:token", tier=2,
         written_under="current config",
         why="a non-burn metric on the project whose burn mechanism is REFUTED. The refuted "
             "branch is scoped to BURN_METRICS and must not leak"),
    dict(transition="control", date="2026-09-14", project="Bitcoin",
         metric="total_supply", value=19_900_000.0, source="coingecko", tier=1,
         written_under="current config",
         why="a tier 1 row with no contract behind it at all — the guards must return early"),
]


def _evaluate(rows: list[dict]) -> dict:
    """Run the CURRENT build_workbook over the fixture rows and record what it produces.

    One aggregate() call over all rows together, not one per row, because two of the
    transitions (changed_measuring_point, and n_points on the orphan series) only exist when
    the sibling rows are present. Evaluating rows in isolation would quietly drop them.
    """
    long = pd.DataFrame([{"date": pd.Timestamp(r["date"]), "project": r["project"],
                          "metric": r["metric"], "value": r["value"], "source": r["source"],
                          "tier": r["tier"], "is_manual": False, "entered_on": ""}
                         for r in rows])
    with forced_dispute():
        out = bw.aggregate(long, pd.DataFrame(), pd.Timestamp(ASOF),
                           gaps=pd.DataFrame(), review=pd.DataFrame())
    by_key = {(r["project"], r["metric"]): r for r in out.to_dict("records")}

    seen, expectations = set(), []
    for r in rows:
        key = (r["project"], r["metric"])
        if key in seen:
            continue                     # one expectation per (project, metric), not per row
        seen.add(key)
        got = by_key[key]
        expectations.append({
            "transition": r["transition"],
            "project": r["project"], "metric": r["metric"],
            "written_under": r["written_under"], "why": r["why"],
            "expect_status": got["status"],
            "expect_confidence": got["confidence"],
            # pd.isna, NOT `is None`. aggregate() returns a DataFrame, so a blanked
            # cell is None only while the column stays object dtype — add one real
            # number anywhere in that column and it promotes to float64 and the None
            # becomes NaN. `is None` then silently reports "value shown" for a cell
            # that is blank. This fixture caught exactly that on its first run.
            "expect_blank": bool(pd.isna(got["now"])),
            "expect_reason_contains": _marker(got["why_amber"]),
        })
    return {"generated": ASOF, "asof": ASOF,
            "note": "Generated by tests/refresh_stale_fixture.py. Do NOT hand-edit — and do not "
                    "refresh to make a failing test pass. Read the diff first.",
            "transitions": list(TRANSITIONS),
            "rows": rows, "expectations": expectations}


def _marker(reason: str) -> str:
    """A short, stable slice of the reason — enough to pin WHICH branch fired, not the prose."""
    reason = str(reason or "")
    # "OF THE CUMULATIVE" rather than the whole opening clause: implausible_delta interpolates
    # the actual share ("ONE OBSERVATION IS 99% OF THE CUMULATIVE"), so anything to the left of
    # it moves with the data and would pin the figure instead of the branch.
    for m in ("ORPHANED", "MEASURING CONTRACT WITHDRAWN", "DERIVATION SUPPRESSED",
              "MEASURING POINT CHANGED", "MECHANISM REFUTED", "OF THE CUMULATIVE",
              "DESTINATION DISPUTED", "not applicable", "no value in the store"):
        if m in reason:
            return m
    return ""


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = _evaluate(ROWS)

    covered = {e["transition"] for e in data["expectations"]}
    missing = set(TRANSITIONS) - covered
    if missing:
        print(f"REFUSING TO WRITE: no row covers {sorted(missing)}", file=sys.stderr)
        return 1

    OUT.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {OUT} — {len(data['rows'])} rows, "
          f"{len(data['expectations'])} expectations, {len(covered)} transitions")
    for e in data["expectations"]:
        blank = "blank" if e["expect_blank"] else f"value shown"
        print(f"  {e['transition']:<24} {e['project']:<12} {e['metric']:<24} "
              f"{e['expect_status']:<11} {e['expect_confidence']:<5} {blank:<11} "
              f"{e['expect_reason_contains']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
