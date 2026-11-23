# Round 17 — frozen moves (one table for the round)

Speed mode (Jake 2026-11-23): one table per round replaces per-cell declarations. The machine-readable copy that `project_freeze.py --compare all` reads is `tests/fixtures/frozen_moves_r17.json`. Values are on the 15-dump fixture build (as of 2026-10-13), not Jake's store.

Reason for every row: R17.A3 — holder value yield divides by the average market cap over its own window (was today's market cap). `<absent>` rows are the header's rename: the figure is carried under the new name.

| Project | Cell | Before | After |
|---|---|---|---|
| Aerodrome | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | <absent> | 10.0679% |
| Aerodrome | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | 6.1923% | <absent> |
| Aerodrome | audit / CP Comparability / Holder value RECORDED, NOT CREDITED (treasury / emissions-funded) ÷ av… | <absent> | 0.2634% |
| Aerodrome | audit / CP Comparability / Holder value RECORDED, NOT CREDITED (treasury / emissions-funded) ÷ ma… | 0.1620% | <absent> |
| Aerodrome | audit / CP Comparability / Holder value yield — fees only (where liquid bribes are credited: Aero… | 5.6441% | 9.1766% |
| Aerodrome | reader / A3 Revenue Buyback / Holder value yield | 6.1923% | 10.0679% |
| Aerodrome | reader / Comparison / Holder value yield | 6.1923% | 10.0679% |
| Aerodrome | reader / Summary / Holder value yield | 6.1923% | 10.0679% |
| Ether.fi | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | <absent> | 0.0000% |
| Ether.fi | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | 0.0000% | <absent> |
| Ether.fi | audit / CP Comparability / Holder value RECORDED, NOT CREDITED (treasury / emissions-funded) ÷ av… | <absent> | 0.2033% |
| Ether.fi | audit / CP Comparability / Holder value RECORDED, NOT CREDITED (treasury / emissions-funded) ÷ ma… | 0.1656% | <absent> |
| Fluid | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | <absent> | 0.0000% |
| Fluid | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | 0.0000% | <absent> |
| Hyperliquid | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | <absent> | 1.5592% |
| Hyperliquid | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | 1.6911% | <absent> |
| Hyperliquid | reader / A3 Revenue Buyback / Holder value yield | 1.6911% | 1.5592% |
| Hyperliquid | reader / Comparison / Holder value yield | 1.6911% | 1.5592% |
| Hyperliquid | reader / Summary / Holder value yield | 1.6911% | 1.5592% |
| Maple | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | <absent> | 1.0721% |
| Maple | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | 0.8541% | <absent> |
| Maple | reader / A3 Revenue Buyback / Holder value yield | 0.8541% | 1.0721% |
| Maple | reader / Comparison / Holder value yield | 0.8541% | 1.0721% |
| Maple | reader / Summary / Holder value yield | 0.8541% | 1.0721% |
| Morpho | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | <absent> | 0.0000% |
| Morpho | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | 0.0000% | <absent> |
| Pendle | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | <absent> | 1.0716% |
| Pendle | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | 0.9421% | <absent> |
| Pendle | audit / CP Comparability / Trend — cp_hvy (arrow · QoQ · 4Q growth/yr · quarters improved; Trajec… | – · NEW since Q-2 · 0/1 improved · QoQ -37.6% | – · NEW since Q-2 · 0/1 improved · QoQ -31.8% |
| Pendle | reader / A3 Revenue Buyback / Holder value yield | 0.9421% | 1.0716% |
| Pendle | reader / Comparison / Holder value yield | 0.9421% | 1.0716% |
| Pendle | reader / Summary / Holder value yield | 0.9421% | 1.0716% |
| Sky | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | <absent> | 2.3649% |
| Sky | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | 2.4435% | <absent> |
| Sky | reader / A3 Revenue Buyback / Holder value yield | 2.4435% | 2.3649% |
| Sky | reader / Comparison / Holder value yield | 2.4435% | 2.3649% |
| Sky | reader / Summary / Holder value yield | 2.4435% | 2.3649% |
| Uniswap | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | <absent> | 2.5546% |
| Uniswap | audit / CP Comparability / HOLDER VALUE YIELD = revenue-funded $ retired + $ distributed + liquid… | 1.9399% | <absent> |
| Uniswap | audit / CP Comparability / Holder value RECORDED, NOT CREDITED (treasury / emissions-funded) ÷ av… | <absent> | 0.0000% |
| Uniswap | audit / CP Comparability / Holder value RECORDED, NOT CREDITED (treasury / emissions-funded) ÷ ma… | 0.0000% | <absent> |
| Uniswap | audit / CP Comparability / Trend — cp_hvy (arrow · QoQ · 4Q growth/yr · quarters improved; Trajec… | – · NEW since Q-3 (fee switch executed 2025-12-27) · 2/2 imp | – · NEW since Q-3 (fee switch executed 2025-12-27) · 2/2 imp |
| Uniswap | reader / Comparison / Holder value yield | 1.9399% | 2.5546% |
| Uniswap | reader / Summary / Holder value yield | 1.9399% | 2.5546% |

44 cells.
