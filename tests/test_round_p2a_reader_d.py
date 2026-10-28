"""P2a Part D on the reader: Aerodrome's park reason comes from config.aerodrome_park_reason (D2) on the as-of day,
and Hyperliquid's market cap comment states its circulating basis (D6)."""
import pandas as pd

import config
import reader


def _reset(day="2026-10-29"):
    reader._AERO_PARK.clear()
    reader._AERO_PARK["day"] = pd.Timestamp(day)


def test_d2_aerodrome_parked_while_the_config_gives_a_reason(monkeypatch):
    seen = {}

    def fake(state=None, day=None):
        seen["day"] = day
        return "PARKED — migration: no v3 rows held yet (MIGRATION WATCH built = None)"
    monkeypatch.setattr(config, "aerodrome_park_reason", fake)
    _reset()
    why = reader.parked_reason("Aerodrome")
    assert why.startswith("PARKED — migration: ")
    for word in ("now", "still", "fixed"):                          # P2c-3: a standalone statement
        assert f" {word} " not in f" {why.lower()} "
    assert why == config.AERODROME_PARK_PLAIN                   # P2c-3: one plain sentence on the reader
    assert seen["day"] == pd.Timestamp("2026-10-29")          # judged on the as-of day, not the wall clock


def test_d2_aerodrome_unparked_when_the_config_gives_none(monkeypatch):
    monkeypatch.setattr(config, "aerodrome_park_reason", lambda state=None, day=None: "")
    _reset()
    assert reader.parked_reason("Aerodrome") == ""
    assert reader.parked_reason("Sky") == ""
    assert reader.parked_reason("World Mobile").startswith("PARKED")


def test_d2_the_real_condition_parks_aerodrome_today():
    # D2: MIGRATION WATCH built is unset and the filing wallets' new-token holdings have no reader.
    _reset("2026-10-27")
    assert reader.parked_reason("Aerodrome").startswith("PARKED — migration")


def test_d6_hyperliquid_market_cap_comment_states_the_basis():
    note = reader._mcap_basis_note(config, "Hyperliquid")
    for part in ("tokenDetails.circulatingSupply", "Hyper Foundation", "Community Grants", "HyperEVM burn",
                 "CoinGecko", "change of basis"):
        assert part in note
    assert reader._mcap_basis_note(config, "Sky") == ""
