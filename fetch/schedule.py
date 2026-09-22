"""
fetch/schedule.py — tier 1: deterministic issuance declared in config.

Bitcoin, Zcash, Bittensor and Venice publish issuance as a schedule, not as a series to fetch.
A schedule is a documented rule, so it belongs in tier 1 alongside the free APIs; the source
string says schedule:config so it is never mistaken for a measured figure.
"""
from __future__ import annotations

import pandas as pd

import config

from .base import LONG_COLUMNS, today

SOURCE = "schedule:config"
TIER = 1


class Schedule:
    def run(self, projects: list[dict], window_days, out):
        now = today()
        for p in projects:
            # ===== A DECLARED ZERO IS A FIGURE, NOT AN EMPTY CELL. Added 2026-09-22. =====
            # Fluid's emissions_tokens gapped with "THE EMISSION HAS FINISHED" — an ANSWER
            # wearing a question's clothes. It states the flow is zero and then leaves the cell
            # blank, so the sheet shows nothing where the truth is 0, and the row sits on a
            # to-do list nobody can action. Where a project DECLARES the zero, it is stored.
            #
            # THE SOURCE STRING SAYS IT IS A DECLARATION. schedule:config:declared, so nothing
            # downstream can mistake it for a measurement, and the confidence machinery reads
            # the state rather than being told an opinion.
            for metric, spec in (p.get("declared_zero") or {}).items():
                if metric not in config.metrics_for_project(p):
                    continue
                out.add(pd.DataFrame({"date": [now], "project": [p["name"]], "metric": [metric],
                                      "value": [0.0], "source": [f"{SOURCE}:declared"],
                                      "tier": [TIER]})[LONG_COLUMNS],
                        SOURCE, p["name"],
                        f"{metric}=0, DECLARED not measured: {spec['why']} "
                        f"(declared by {spec['declared_by']}"
                        + ("" if spec.get("sourced") else
                           f"; NOT yet sourced — {spec.get('still_needed')}") + ")", TIER)

            sched = p.get("issuance_schedule")
            if not sched or not sched.get("steps"):
                continue
            steps = sorted(sched["steps"], key=lambda s: s["from"])
            start = pd.Timestamp(steps[0]["from"])
            if window_days is not None:
                start = max(start, now - pd.Timedelta(days=window_days))
            if start > now:
                continue
            dates = pd.date_range(start, now, freq="D")
            # Each step covers from its own date until whichever comes first: its declared
            # "until", the next step, or today. The "until" matters for a DECLINING curve — without
            # it the final rate is carried forward for ever, reporting last year's higher issuance
            # as this year's. Past a final "until" the schedule has nothing to say and says nothing.
            per_day = pd.Series(index=dates, dtype=float)
            for idx, step in enumerate(steps):
                begins = pd.Timestamp(step["from"])
                if step.get("until"):
                    ends = pd.Timestamp(step["until"])
                elif idx + 1 < len(steps):
                    ends = pd.Timestamp(steps[idx + 1]["from"]) - pd.Timedelta(days=1)
                else:
                    ends = dates[-1]
                per_day[(per_day.index >= begins) & (per_day.index <= ends)] = float(step["tokens_per_day"])
            per_day = per_day.dropna()
            if per_day.empty:
                continue
            metrics = ["gross_issuance_tokens"]
            # Where the schedule IS emissions to stakers (Aave's stkAAVE allowance top-ups), the same
            # figure feeds emissions_tokens as well, so net absorption nets it off rather than
            # counting the buyback alone and overstating the result.
            if sched.get("also_emissions"):
                metrics.append("emissions_tokens")
            for metric in metrics:
                df = pd.DataFrame({"date": per_day.index, "project": p["name"],
                                   "metric": metric, "value": per_day.values,
                                   "source": SOURCE, "tier": TIER})
                out.add(df[LONG_COLUMNS], SOURCE, p["name"], sched.get("note", "issuance schedule"), TIER)
