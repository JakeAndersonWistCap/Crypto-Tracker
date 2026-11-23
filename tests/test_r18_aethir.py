"""Round 18 item 7 (Jake 2026-11-24): Aethir's subsidy-cover allowance and its three MATURING rows run to 2026-12-31,
each only where its recorded reason still holds."""
import config


def test_the_four_items_are_extended_to_the_end_of_december_and_no_other():
    assert config.R18_AETHIR_EXTENDED["items"] == [
        "REGRESSION_ALLOWLIST Aethir SUBSIDY COVER", "CREDIBILITY Aethir in_locked", "CREDIBILITY Aethir in_arr",
        "CREDIBILITY Aethir a2_customer_revenue"]
    a = [x for x in config.REGRESSION_ALLOWLIST if x["project"] == "Aethir" and "SUBSIDY COVER" in x["match"]]
    assert len(a) == 1 and a[0]["until"] == "2026-12-31"
    assert a[0]["while_outflow_incomplete"] == "b" and ("Aethir", "b") in config.OUTFLOW_INCOMPLETE   # its condition stands
    others = {x["project"]: x["until"] for x in config.REGRESSION_ALLOWLIST if "SUBSIDY COVER" in x["match"]
              and x["project"] != "Aethir"}
    assert others == {"Pendle": "2026-11-14", "Fluid": "2026-12-16"}           # nobody else's date moved
    c = config.CREDIBILITY["Aethir"]
    assert c["in_locked"]["ref"]["verdict"] == c["in_arr"]["ref"]["verdict"] == "MATURING (until 2026-12-31)"
    assert "2026-12-31" in c["a2_customer_revenue"]["fresh_label"]
