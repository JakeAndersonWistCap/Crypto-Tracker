"""Project round 1 (Jake 2026-11-05): the lead's merge fixes. No network."""
from types import SimpleNamespace

import reader


class _A:
    def __init__(self, fields):
        self.fields = fields

    def data_field(self, key, header):
        return self.fields.get(header)


class _Data:
    def __init__(self):
        self.vals = {}

    def put(self, name, key, v):
        self.vals[key] = v
        return f"_data!{key}"


def _run(monkeypatch, fields):
    out = {}
    monkeypatch.setattr(reader, "put_code", lambda sh, r, c, code, why, *a, **k: out.update(code=code, why=why))
    monkeypatch.setattr(reader, "put_formula", lambda sh, r, c, f, *a, **k: out.update(formula=f, note=a[1]))
    ctx = SimpleNamespace(A=_A(fields), data=_Data())
    reader._thirty_day_change(None, ctx, 1, None, {"name": "Uniswap"}, "revenue_usd", "revenue")
    return out, ctx.data.vals


def test_thirty_day_change_is_a_rate_per_covered_day_with_the_read_lag_allowed(monkeypatch):
    """PR1.U1 x PR1.S1: the latest window covers 29 days and 1 day of read lag (the as-of day, not read); the change
    divides per-covered-day rates, so 29 days are not compared with 30 as if they were 30."""
    out, vals = _run(monkeypatch, {reader._DATA_NOW: 290.0, reader._DATA_M1: 300.0, reader._DATA_NOW_DAYS: 29,
                                   reader._DATA_M1_DAYS: 30, reader._DATA_LAG: 1})
    assert "formula" in out, out
    f = out["formula"]
    rate = (vals["revenue30|now"] / vals["revenue30|now_days"]) / (vals["revenue30|prior"] / vals["revenue30|prior_days"]) - 1
    assert abs(rate) < 1e-12          # 10 a day both windows: no change (the old 29-of-30 sum would read -3.3%)
    assert f.startswith("=(") and "now_days" in f and "prior_days" in f
    assert "read lag" in out["note"]


def test_thirty_day_change_refuses_a_hole_that_is_not_the_read_lag(monkeypatch):
    out, _ = _run(monkeypatch, {reader._DATA_NOW: 250.0, reader._DATA_M1: 300.0, reader._DATA_NOW_DAYS: 25,
                                reader._DATA_M1_DAYS: 30, reader._DATA_LAG: 1})
    assert out.get("code") == "n/a" and "never 0" in out["why"]
