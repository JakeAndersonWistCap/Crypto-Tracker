"""Project round 1B (Jake 2026-11-09): the lead's merge fixes and the re-freeze. No network."""
import config


def test_uniswap_burn_split_note_agrees_with_the_numbers():
    """PR1B.MERGE: the A4 Notes cell still said 'implied burn = protocol fees ÷ avg price' (PR1B.3 made it each day's
    price) and its comment 'with the 100M one-off out' (P5-1C.B1 counts it in the burn)."""
    note = config.PROJECT_BY_NAME["Uniswap"]["burn_split"]["note"]
    assert "each day's price" in note and "avg price" not in note
    assert "TokenJar" in note and "PR1B.9" in note
    assert "less the" not in note and "counted in its window" in note
    src = open(config.__file__, encoding="utf-8").read()
    assert "with the\n        # 2025-12-27 100,000,000 one-off out" not in src


def test_every_round_1b_uniswap_move_is_declared_with_its_item_and_date():
    """PR1B.FREEZE: each of the 68 moves names the round-1B item that moved it, on 2026-11-09."""
    moves = config.FROZEN_MOVES["Uniswap"]
    assert len(moves) == 68
    for key, d in moves.items():
        assert key.split("|", 1)[0] in ("audit", "reader"), key
        assert d["date"] == "2026-11-09" and d["why"].startswith("PR1B."), (key, d)
        assert "to" in d, key
    renamed_away = [k for k, d in moves.items() if d["to"] == "<absent>"]
    assert len(renamed_away) == 19 and all("HEADLINE_RENAMES" in moves[k]["why"] for k in renamed_away)
