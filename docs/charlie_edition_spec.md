# Charlie edition of the reader workbook — build specification (Jake, 2026-10-29; SPEC.md P2g)

A "Charlie edition" of the READER workbook for a walkthrough with the fund manager on 2026-10-30. Additive only. Small
budget: small steps, reuse existing helpers in reader.py / build_workbook.py, no refactor.

## HARD RULES
- No change to any definition, formula, data, rank, code or existing tab of the normal build. With the flag absent,
  normal outputs are identical to today. A test proves it (compare reader cell values with and without the flag on the
  fixture build).
- Cell fills mean confidence only (green/amber/red), as About says. Use font colour, bars and borders for everything else.
- Text on the new tab reads as a fresh standalone page: no "still", "now", "newly", "improved since", no reference to
  earlier builds.
- Every number on the Highlights tab is a formula pointing at the cell it quotes, found at build time by tab + project
  row + header text (never hard-coded addresses), with the source cell's number format. No number is typed into a sentence.
- Parked projects (Aerodrome, World Mobile) never appear in a card or a mini-table; they appear only in the coverage block.

## 1. FLAG
`python token_metrics.py --no-fetch --edition charlie` writes, in addition to the normal files,
`token_metrics_charlie.xlsx` (reader only, no audit file). Without the flag, nothing changes.

## 2. NEW FIRST TAB "Highlights" (teal tab colour), A4 landscape, fit to one page wide, gridlines off, zoom 90%
Top: title "Token metrics — highlights"; as-of date (same as About); one line copied from the first sentence of About's
"What this file measures"; one line copied from About's "Fill is confidence ..." sentence.
Then cards in two columns. A card = bold navy headline; up to 4 chips (small label above, large value below); one
"Reading" sentence; one "Caveat" sentence; an internal hyperlink "Open <tab>". Each card has a build-time condition.
A card whose condition fails is omitted and the others reflow. At least 5 cards must render, otherwise the build fails
with a clear message listing the failed conditions.

Cards (chips are [tab: header]):

C1 GEODNET — "Customer revenue exceeds the emissions that pay for it". Chips: [A2: Customer revenue ÷ emissions (x)];
the same key for the three earlier quarters from the A2 trajectory columns; [Comparison: Holder value yield].
Condition: key >= 1.0 and key > -1q > -2q > -3q. Reading: "The key has risen every quarter shown and sits above the 1.0x
crossover." Caveat: "Net holder yield is negative: dilution still exceeds what reaches holders."
Expected on the 2026-10-29 build: 1.06x, 0.86x, 0.79x, 0.73x, 6.8%.

C2 Morpho — "Customer revenue is many times the emissions that pay for it". Chips: [A2 key]; [A2 key, -3 quarters];
[Summary: Protocol revenue, annualised]; [Business: TVL change, selected period]. Condition: key >= 10 and key >= 1.3 x the
-3 quarters value. Reading: "The business earns far more than it pays out in tokens; none of that revenue reaches the
token yet (protocol revenue is a measured value; see the cell comment)." Caveat: "Holder yield is n/a until revenue
reaches the token; this is value created, not yet captured." Expected: 22.95x, 14.25x, $0, +45.9%.

C3 Sky — "Holders are paid more than they are diluted". Chips: [Comparison: Net holder yield], [Holder value yield],
[Total dilution, % of float], [Lock rate, % of circulating]. Condition: highest Net holder yield among non-parked projects
and > 0 (if another project is highest use that project's name; if none > 0 omit). Reading: "Measured returns to holders
exceed measured dilution; the only project in the set where that holds." (say "the highest in the set" if more than one is
positive; add "most of the circulating supply is locked" only if lock rate > 0.5). Caveat: "The buyback retires well under
1.0x of what is emitted, and revenue growth is flat over the selected period." Expected: +3.2%, 3.9%, 6.2%, 85.7%.

C4 Hyperliquid — "Buyback retires as many tokens as the protocol emits". Chips: [A3: Retired ÷ emissions (x)] and its three
earlier quarters; [Summary: Protocol revenue, annualised]; [Summary: Revenue growth, selected period]. Condition: Summary
Rank in archetype = 1 and A3 key >= 0.9. Reading: "Rank 1 in A3, at the break-even line between retired and emitted
tokens" (add ", on the largest revenue base in the set" only if its Protocol revenue, annualised is the largest among
non-parked). Caveat: "The key has narrowed over three quarters, net holder yield is negative, and the A4 crossover is
blocked for lack of same-span coverage." Expected: 1.02x; 1.01x, 1.75x, 2.57x; $849m; +48.6%.

C5 Uniswap — "Protocol revenue has scaled and supply is shrinking". Chips: [Summary: Protocol revenue, annualised];
[Summary: Revenue growth, selected period]; [A4: Permanent burn yield]; [Summary: Net supply change, % a year].
Condition: growth > 0.5 and net supply change < 0. Reading: "Revenue is wired to the token and the circulating supply is
falling." Caveat: "The A4 key is n/a (nothing issued to divide by) and net holder yield is negative."
Expected: $126.6m, +140.6%, 3.6%, -3.6%.

C6 Fluid — "The tracker shows the buyback pause". Chips: [A3 key] latest and the three earlier quarters; [Comparison:
Subsidy cover (x)]; [Comparison: Net holder yield]. Condition: latest = 0 and max of the three earlier > 0. Reading:
"Retired ÷ emissions fell to zero over four quarters while revenue grew and subsidy cover is high" (add "net holder yield
is second best in the set" only if true). Caveat: "A pause is a policy choice; the key does not judge intent."
Expected: 0, 0, 0.65x, 1.23x; 56.4x; -0.9%.

C7 Near — "Fastest-growing business in the set". Chips: [Summary: Revenue growth, selected period]; [Business: TVL change,
selected period]; [Business: Price change, selected period]; [Comparison: Net holder yield]. Condition: highest revenue
growth among non-parked. Reading: "Revenue and TVL are growing faster than any other project over the selected period."
Caveat: "The price moved with it over the same window, so part of the growth is priced in."
Expected: +163%, +99.4%, +160%, -1.3%.

C8 Dilution league (table card, no chips) — the three lowest and three highest [Comparison: Total dilution, % of float]
among non-parked projects that have a value, as project | value rows. Reading: "The same measures that show
improvement show who pays for growth with new tokens." Expected lowest: Ethereum 1.4%, Pendle 3.9%, Near 4.1%; highest:
Aethir 28.8%, Hyperliquid 22.7%, Plume 20.8%.

Bottom "Coverage" block: copy About's honest-coverage table (as formulas referencing it) and list every unranked or parked
project with its code and the reason from its Summary key-cell comment. Final line: "Confidence is shown by cell fill
throughout; hover any number for its source."

## 3. LOOK (edition only)
- Calibri 10; title 18 bold navy #14213D; column headers bold white on navy, wrapped, row height 32; group bands (Size,
  Business, Rank, Holders, Supply) in two alternating blues; thin light-grey borders; zebra #F7F8FA on data rows.
- Freeze panes under the header row and after the Token column on Summary, A1-A4, Comparison, Business. Gridlines off.
  Zoom 90%.
- Sign colours (font only): green #1B7F5C for positive, red #B23A48 for negative, on every column whose header contains
  "growth", "change", "Net holder yield", "Net supply change" or "Staking yield minus"; codes and zero stay neutral.
- Data bars (light teal) on Comparison: Holder value yield, Total dilution % of float; diverging bar on Net holder yield.
  No data bars on mixed-unit columns.
- Codes (n/a, blocked, by design, parked, unranked, secondary): grey italic, centred. Existing parked-row treatment kept.
- Tab order: Highlights, Summary, A1-A4, Comparison, Business, Charts, About, Definitions. Tab colours: Highlights teal;
  Summary navy; A1-A4 mid-blue; Comparison and Business slate; Charts, About, Definitions light grey.
- Token counts >= 1e6 shown as 1.2m / 3.4bn, same convention as dollars without the $. All other number formats unchanged.
Use whichever library the existing reader uses; add no new libraries.

## 5. VERIFY
Run the new tests, tests/test_progress.py and the guard tests first; then the full suite once at the end. Recalculate the
Charlie edition in LibreOffice and report formulas, mismatches and errors. Fail the build if any Highlights chip evaluates
to an error or blank. At the end of the build, print the rendered cards and their chip values (as displayed) so they can
be read in PowerShell.
