"""Project round 1D (Jake 2026-11-10): the Credibility FDV row is judged by its inputs. No network."""
import pandas as pd

import config
import credibility as cred


def test_fdv_row_is_judged_by_price_and_total_supply_with_coingecko_beside():
    """FDV = price x total supply: 'PASS (inputs)' when both legs pass on independent routes — the price vs Coinbase
    where a product is declared (Uniswap), else DefiLlama's for the token's address — and the total supply row.
    CoinGecko's FDV re-reads CoinGecko's price, so it stays beside the row as information, never the judge."""
    rows = {"Uniswap|fdv_usd_coingecko": {"now": 6.73e9, "latest_date": "2026-10-13"},
            "Uniswap|total_supply_coingecko": {"now": 8.87e8, "latest_date": "2026-10-13"}}
    spec = cred.wiring_inputs("Uniswap", rows)["in_fdv_coingecko"]
    assert spec["inputs"] == ("in_price", "in_total_supply_coingecko")
    assert config.CREDIBILITY_XREF["coinbase_product"].get("Uniswap")
    other = next(n for n in config.CREDIBILITY_PROJECTS if not config.CREDIBILITY_XREF["coinbase_product"].get(n))
    spec_o = cred.wiring_inputs(other, {f"{other}|fdv_usd_coingecko": {"now": 1.0e9}})["in_fdv_coingecko"]
    assert spec_o["inputs"] == ("in_price_llama", "in_total_supply_coingecko")
    out = cred.build_rows([], {**rows, "Uniswap|fdv_usd": {"now": 6.73e9, "latest_date": "2026-10-13"}},
                          pd.DataFrame(columns=["date", "project", "metric", "value", "source"]),
                          pd.Timestamp("2026-10-14"), projects={"Uniswap"})
    fdv = next(r for r in out if r["project"] == "Uniswap" and r["id"] == "in_fdv_coingecko")
    assert fdv["mode"] == "derived" and fdv["tol"] is None and fdv["verdict"] is None
    assert fdv["value"] == 6.73e9 and "information only" in fdv["source"]          # CoinGecko's FDV beside it
    judged = {out[i]["id"] for i in fdv["input_rows"]}
    assert judged == {"in_price", "in_total_supply_coingecko"} and not fdv["missing_inputs"]
    assert "Judged by: in_price, in_total_supply_coingecko" in fdv["note"]


def test_a_parked_project_still_records_coingeckos_fdv():
    rows = {"World Mobile|fdv_usd_coingecko": {"now": 1.0e8, "latest_date": "2026-10-20"},
            "World Mobile|fdv_usd": {"now": 1.1e8, "latest_date": "2026-10-20"}}
    out = cred.build_rows([], rows, pd.DataFrame(), pd.Timestamp("2026-10-21"), projects={"World Mobile"})
    wm = [r for r in out if r["project"] == "World Mobile"]
    assert len(wm) == 1 and wm[0]["verdict"] == "N/A (recorded)"
