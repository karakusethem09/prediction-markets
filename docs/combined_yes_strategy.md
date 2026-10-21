# Combined YES Strategy: a retrospective, hypothetical analysis

*Generated 2026-10-21 05:59 UTC by `src/analysis/combined_yes_strategy.py` from the project's online PostgreSQL database (Supabase), read with the dashboard's read-only login. Every number below is computed from recorded prices and outcomes; nothing is estimated by hand.*

> **This is not a validated or executable trading strategy.** The entry price is taken 7 days before each market's *actual* close, which a trader would not have known in advance, and recorded prices are not executable bid/ask quotes. Read the results as a description of past prices, not as returns that could have been earned. See section 10.

## Summary: the latest 100 markets

- **Sample.** The latest 100 qualifying markets split naturally into Group A: 28, Group B: 72, from 90 distinct events.
- **Group A** (0.20–0.30): YES won 13 of 28 (46.4%) against an average price of 0.253, so one share each would have made $5.93 (ROI 83.9%).
- **Group B** (0.50–0.60): YES won 44 of 72 (61.1%) against an average price of 0.539, so one share each would have made $5.17 (ROI 13.3%).
- **Combined, gross.** $11.10 on $45.90 paid: ROI 24.2% (one share per market).
- **With costs.** +$0.01/share: $10.10 (ROI 21.5%); +$0.02/share: $9.10 (ROI 19.0%).
- **Uncertainty.** Resampling whole events, the 95% interval for the combined gross P/L is $1.92 to $19.96. It excludes zero, but that alone does not make the result tradable (see limitations).
- **Which group drives it.** Both groups were profitable.
- **Earlier sample.** The 100 qualifying markets before these made $2.31 combined (ROI 5.2%, edge +2.3 pts): Group A $3.73 (edge +12.9 pts); Group B -$1.42 (edge -2.0 pts). Not every group pointed the same way as in the latest sample, so the pattern is not stable across these two periods.

## Summary: all 992 eligible markets

The same rules on every qualifying market in the database (section 12), to see whether the latest 100 were typical.

- **All eligible markets.** 992 markets from 862 events, closing 2024-03-05 to 2026-10-20.
- **Group A:** 372 markets, win rate 37.1% vs average price 0.250 (edge +12.1 pts), $44.92 on $93.08 paid: ROI 48.3%, 95% interval 29.6% to 67.2% (interval above zero).
- **Group B:** 620 markets, win rate 55.0% vs average price 0.537 (edge +1.3 pts), $7.87 on $333.13 paid: ROI 2.4%, 95% interval -5.1% to 9.6% (interval includes zero).
- **Combined:** 992 markets, win rate 48.3% vs average price 0.430 (edge +5.3 pts), $52.79 on $426.21 paid: ROI 12.4%, 95% interval 5.3% to 19.4% (interval above zero).

## Summary: without the look-ahead

The original entry rule uses each market's *actual* close, which a trader could not know. Section 13 removes that advantage in two ways.

- **Where the edge came from.** Markets that closed more than a day *early* had an edge of +19.2 pts; markets that closed on schedule +2.8 pts. Early closes often happen because YES happened, which the original entry rule quietly exploits.
- **Closed on schedule.** Group A: 245 markets, edge +10.1 pts, ROI 40.5% (95% interval 17.3% to 64.5%, above zero; 30.1% with +$0.02/share); Group B: 506 markets, edge -0.7 pts, ROI -1.4% (95% interval -9.6% to 7.0%, includes zero; -4.9% with +$0.02/share); combined ROI 6.3% (includes zero).
- **Entry 7 days before the scheduled end.** Group A: 390 markets, edge +8.3 pts, ROI 33.3% (95% interval 14.9% to 52.4%, above zero; 23.4% with +$0.02/share); Group B: 761 markets, edge -1.7 pts, ROI -3.2% (95% interval -9.6% to 3.5%, includes zero; -6.7% with +$0.02/share); combined ROI 3.8% (includes zero).

## 1. Research question

The 7-day calibration chart suggests that markets priced **20–30%** and **50–60%** seven days before close resolved YES more often than their prices implied. If so, buying YES in those two intervals would have earned more than it cost. Did it, on the most recent markets, and how sure can we be?

## 2. Strategy rules

- **Group A:** YES price in [0.20, 0.30). **Group B:** YES price in [0.50, 0.60). The lower bound is included, the upper one excluded.
- Buy YES at the 7-day snapshot price and hold until the market resolves.
- **One-share scenario:** exactly one share per market; profit = payout − price − costs; ROI = total profit ÷ total paid (aggregated over all shares, not an average of per-trade ROIs).
- **Equal-dollar scenario:** $1 per market, buying $1 ÷ (price + costs) shares; ROI = total profit ÷ total invested. Cheap contracts get more shares, so the two scenarios weigh trades differently and are reported separately.
- The intervals were fixed before looking at these results and are not tuned afterwards.

## 3. Selection methodology

- Markets with a usable **7-day snapshot** (the latest price at or before 7 days before close, at most 1 day old, as defined by the project's snapshot rule) and a binary final outcome.
- Price in Group A or Group B. A 30-day snapshot is not required.
- Sorted by close time, newest first, with `market_id` breaking ties; the first **100 in total** across both groups. The split between groups is whatever occurs naturally.
- Events: markets that share an event (e.g. several outcomes of one election) are grouped by `event_id`; a market without one is its own event.

## 4. The latest 100 qualifying markets

Closing between 2026-09-09 and 2026-10-20.

| # | Closed | Group | Market | Event | 7-day price | YES won? | P/L (1 share) |
|---|---|---|---|---|---|---|---|
| 1 | 2026-10-20 | A | Commanders vs. 49ers | poly:987958 | 0.225 | no | -$0.23 |
| 2 | 2026-10-19 | B | Tampa Bay Rays vs. Cleveland Guardians | poly:1170777 | 0.500 | yes | $0.50 |
| 3 | 2026-10-18 | A | Chargers vs. Chiefs | poly:982305 | 0.200 | no | -$0.20 |
| 4 | 2026-10-18 | B | Will Olympique Lyonnais win on 2026-10-18? | poly:1134923 | 0.575 | yes | $0.43 |
| 5 | 2026-10-18 | B | Steelers vs. Buccaneers | poly:982303 | 0.570 | yes | $0.43 |
| 6 | 2026-10-18 | B | Saints vs. Giants | poly:982301 | 0.530 | yes | $0.47 |
| 7 | 2026-10-18 | B | Bears vs. Falcons | poly:982297 | 0.550 | yes | $0.45 |
| 8 | 2026-10-18 | A | Titans vs. Colts | poly:982299 | 0.270 | yes | $0.73 |
| 9 | 2026-10-18 | A | Nebraska vs. Oregon | poly:1139636 | 0.220 | no | -$0.22 |
| 10 | 2026-10-17 | B | Washington vs. Purdue | poly:1139604 | 0.525 | yes | $0.47 |
| 11 | 2026-10-16 | B | Will Eintracht Frankfurt win on 2026-10-16? | poly:1122456 | 0.515 | yes | $0.48 |
| 12 | 2026-10-16 | B | Seahawks vs. Broncos | poly:963984 | 0.560 | no | -$0.56 |
| 13 | 2026-10-15 | B | Will Bayer 04 Leverkusen win on 2026-10-15? | poly:1115111 | 0.590 | yes | $0.41 |
| 14 | 2026-10-14 | B | Will Real Madrid CF win on 2026-10-14? | poly:1114980 | 0.515 | yes | $0.48 |
| 15 | 2026-10-14 | B | Spread: Liverpool FC (-1.5) | poly:1109629 | 0.585 | yes | $0.42 |
| 16 | 2026-10-14 | A | Florida International vs. Jacksonville State | poly:1139595 | 0.285 | no | -$0.28 |
| 17 | 2026-10-13 | B | Spread: FC Barcelona (-1.5) | poly:1109634 | 0.560 | yes | $0.44 |
| 18 | 2026-10-11 | B | Vikings vs. Saints | poly:941335 | 0.540 | yes | $0.46 |
| 19 | 2026-10-11 | B | Will Real Sociedad de Fútbol win on 2026-10-11? | poly:1096282 | 0.565 | yes | $0.44 |
| 20 | 2026-10-11 | B | Georgia vs. Alabama | poly:1100616 | 0.520 | yes | $0.48 |
| 21 | 2026-10-10 | B | Texas A&M vs. Missouri | poly:1100347 | 0.525 | no | -$0.53 |
| 22 | 2026-10-08 | A | Will San Diego Padres win the 2026 National League Championship Series | poly:215871 | 0.202 | no | -$0.20 |
| 23 | 2026-10-06 | B | Spread: Saints (-1.5) | poly:909452 | 0.545 | no | -$0.55 |
| 24 | 2026-10-05 | B | Will Sweden win on 2026-10-05? | poly:1063318 | 0.550 | yes | $0.45 |
| 25 | 2026-10-05 | A | Will Luiz Inácio Lula da Silva finish in second place in the first rou | poly:204972 | 0.242 | yes | $0.76 |
| 26 | 2026-10-05 | A | Will Flavio Bolsonaro win the most votes in the first round of the 202 | poly:943054 | 0.255 | yes | $0.74 |
| 27 | 2026-10-05 | B | Spread: 49ers (-2.5) | poly:909449 | 0.525 | yes | $0.47 |
| 28 | 2026-10-05 | A | Chargers vs. Seahawks | poly:909447 | 0.260 | no | -$0.26 |
| 29 | 2026-10-04 | B | Will Portugal win on 2026-10-04? | poly:1057948 | 0.500 | yes | $0.50 |
| 30 | 2026-10-04 | B | Will Germany win on 2026-10-04? | poly:1057951 | 0.550 | no | -$0.55 |
| 31 | 2026-10-04 | B | Rams vs. Eagles | poly:909441 | 0.590 | yes | $0.41 |
| 32 | 2026-10-04 | A | Patriots vs. Bills | poly:909433 | 0.290 | yes | $0.71 |
| 33 | 2026-10-04 | B | Packers vs. Buccaneers | poly:909442 | 0.510 | yes | $0.49 |
| 34 | 2026-10-04 | B | Spread: Colts (-4.5) | poly:909431 | 0.500 | yes | $0.50 |
| 35 | 2026-10-04 | A | Washington vs. USC | poly:1060895 | 0.290 | no | -$0.29 |
| 36 | 2026-10-04 | B | Army vs. Louisiana Tech | poly:1060899 | 0.510 | no | -$0.51 |
| 37 | 2026-10-03 | B | Will England win on 2026-10-03? | poly:1052639 | 0.520 | yes | $0.48 |
| 38 | 2026-10-03 | B | Dallas Wings vs. Golden State Valkyries | poly:1081049 | 0.500 | no | -$0.50 |
| 39 | 2026-10-02 | B | Will Belgium win on 2026-10-02? | poly:1047244 | 0.555 | yes | $0.44 |
| 40 | 2026-10-02 | B | Steelers vs. Browns | poly:885112 | 0.560 | no | -$0.56 |
| 41 | 2026-10-01 | B | Will Portugal win on 2026-10-01? | poly:1042358 | 0.500 | yes | $0.50 |
| 42 | 2026-10-01 | B | Will Netherlands win on 2026-10-01? | poly:1042355 | 0.505 | no | -$0.51 |
| 43 | 2026-10-01 | A | Will Wales win on 2026-10-01? | poly:1042356 | 0.215 | yes | $0.79 |
| 44 | 2026-10-01 | B | Will Norway win on 2026-10-01? | poly:1042356 | 0.590 | no | -$0.59 |
| 45 | 2026-10-01 | B | Counter-Strike: FaZe vs Nemiga (BO3) - Stake Ranked Episode 4 Playoffs | poly:1038938 | 0.590 | no | -$0.59 |
| 46 | 2026-09-29 | B | Eagles vs. Bears: O/U 41.5 | poly:870381 | 0.590 | no | -$0.59 |
| 47 | 2026-09-28 | A | Will Belgium win on 2026-09-28? | poly:1027446 | 0.250 | no | -$0.25 |
| 48 | 2026-09-28 | B | Will France win on 2026-09-28? | poly:1027446 | 0.545 | yes | $0.45 |
| 49 | 2026-09-28 | B | Spread: Rams (-1.5) | poly:864764 | 0.530 | no | -$0.53 |
| 50 | 2026-09-28 | B | Rams vs. Broncos: O/U 44.5 | poly:864764 | 0.500 | yes | $0.50 |
| 51 | 2026-09-28 | B | Rams vs. Broncos | poly:864764 | 0.565 | no | -$0.56 |
| 52 | 2026-09-28 | B | Spread: 49ers (-7.5) | poly:864757 | 0.515 | no | -$0.52 |
| 53 | 2026-09-28 | B | Ravens vs. Cowboys | poly:864723 | 0.570 | yes | $0.43 |
| 54 | 2026-09-27 | B | Vikings vs. Buccaneers | poly:864759 | 0.550 | yes | $0.45 |
| 55 | 2026-09-27 | B | Will Norway win on 2026-09-27? | poly:1022042 | 0.540 | no | -$0.54 |
| 56 | 2026-09-27 | B | Spread: Chiefs (-10.5) | poly:864744 | 0.515 | yes | $0.48 |
| 57 | 2026-09-27 | B | Spread: Lions (-6.5) | poly:864734 | 0.565 | yes | $0.44 |
| 58 | 2026-09-27 | B | Seahawks vs. Commanders | poly:864754 | 0.570 | no | -$0.57 |
| 59 | 2026-09-27 | A | Chargers vs. Bills | poly:864726 | 0.290 | no | -$0.29 |
| 60 | 2026-09-27 | B | Bengals vs. Steelers | poly:864751 | 0.585 | no | -$0.58 |
| 61 | 2026-09-27 | B | Texans vs. Colts | poly:864736 | 0.565 | no | -$0.56 |
| 62 | 2026-09-27 | A | Titans vs. Giants | poly:864747 | 0.290 | no | -$0.29 |
| 63 | 2026-09-25 | A | Falcons vs. Packers | poly:848221 | 0.265 | yes | $0.73 |
| 64 | 2026-09-24 | B | Will Norway win on 2026-09-24? | poly:1003664 | 0.515 | yes | $0.48 |
| 65 | 2026-09-22 | B | Spread: Rams (-6.5) | poly:832909 | 0.545 | yes | $0.45 |
| 66 | 2026-09-22 | B | Giants vs. Rams: O/U 47.5 | poly:832909 | 0.520 | no | -$0.52 |
| 67 | 2026-09-22 | A | Giants vs. Rams | poly:832909 | 0.255 | no | -$0.26 |
| 68 | 2026-09-21 | A | Will Bitcoin reach $85,000 in September? | poly:946004 | 0.265 | yes | $0.73 |
| 69 | 2026-09-21 | A | Colts vs. Chiefs | poly:827222 | 0.290 | no | -$0.29 |
| 70 | 2026-09-21 | B | Spread: Broncos (-2.5) | poly:827206 | 0.520 | yes | $0.48 |
| 71 | 2026-09-21 | B | Spread: Chargers (-6.5) | poly:827208 | 0.530 | no | -$0.53 |
| 72 | 2026-09-20 | A | Commanders vs. Cowboys | poly:827216 | 0.290 | no | -$0.29 |
| 73 | 2026-09-20 | A | Raiders vs. Chargers | poly:827208 | 0.255 | yes | $0.74 |
| 74 | 2026-09-20 | A | Browns vs. Buccaneers | poly:827198 | 0.250 | yes | $0.75 |
| 75 | 2026-09-20 | B | Steelers vs. Patriots: O/U 41.5 | poly:827188 | 0.575 | no | -$0.57 |
| 76 | 2026-09-20 | B | Panthers vs. Falcons | poly:827173 | 0.510 | yes | $0.49 |
| 77 | 2026-09-20 | A | Saints vs. Ravens | poly:827176 | 0.205 | yes | $0.80 |
| 78 | 2026-09-20 | A | Will Club Atlético de Madrid win on 2026-09-20? | poly:981053 | 0.280 | yes | $0.72 |
| 79 | 2026-09-20 | B | Will Real Madrid CF win on 2026-09-20? | poly:981053 | 0.545 | no | -$0.55 |
| 80 | 2026-09-20 | B | LSU vs. Ole Miss | poly:985390 | 0.580 | no | -$0.58 |
| 81 | 2026-09-20 | B | SMU vs. Louisville | poly:985327 | 0.500 | no | -$0.50 |
| 82 | 2026-09-19 | B | Will Arsenal FC win on 2026-09-19? | poly:973635 | 0.555 | no | -$0.56 |
| 83 | 2026-09-18 | B | LoL: Team WE vs JD Gaming - Game 3 Winner | poly:982573 | 0.500 | no | -$0.50 |
| 84 | 2026-09-18 | B | LoL: Team WE vs JD Gaming - Game 1 Winner | poly:982573 | 0.500 | yes | $0.50 |
| 85 | 2026-09-18 | B | US x Iran Effective Ceasefire by September 4? | poly:950983 | 0.595 | yes | $0.41 |
| 86 | 2026-09-17 | B | Will Real Betis Balompié win on 2026-09-17? | poly:963032 | 0.525 | yes | $0.47 |
| 87 | 2026-09-16 | B | Will the Fed increase interest rates by 25 bps after the September 202 | poly:481717 | 0.525 | yes | $0.47 |
| 88 | 2026-09-16 | B | Fed Rate Hike by September 2026 Meeting? | poly:329566 | 0.535 | yes | $0.46 |
| 89 | 2026-09-15 | B | Will Liverpool FC win on 2026-09-15? | poly:951618 | 0.525 | yes | $0.47 |
| 90 | 2026-09-14 | A | Will Real Betis Balompié win on 2026-09-14? | poly:946150 | 0.245 | yes | $0.76 |
| 91 | 2026-09-14 | A | Will Alexander Zverev win the 2026 Men's US Open? | poly:139236 | 0.216 | yes | $0.78 |
| 92 | 2026-09-13 | A | Saints vs. Lions | poly:795808 | 0.255 | no | -$0.26 |
| 93 | 2026-09-13 | B | Will SSC Napoli win on 2026-09-13? | poly:938825 | 0.555 | yes | $0.44 |
| 94 | 2026-09-13 | B | Will Brighton & Hove Albion FC win on 2026-09-13? | poly:939679 | 0.520 | yes | $0.48 |
| 95 | 2026-09-13 | B | Will RC Celta de Vigo win on 2026-09-13? | poly:940188 | 0.545 | no | -$0.55 |
| 96 | 2026-09-12 | B | LoL: Team Vitality vs Movistar KOI (BO5) - LEC Playoffs | poly:970967 | 0.500 | no | -$0.50 |
| 97 | 2026-09-09 | B | Will Sporting CP win on 2026-09-09? | poly:931722 | 0.500 | yes | $0.50 |
| 98 | 2026-09-09 | B | Will Arsenal FC win on 2026-09-09? | poly:931723 | 0.535 | yes | $0.46 |
| 99 | 2026-09-09 | A | Will SSC Napoli win on 2026-09-09? | poly:931723 | 0.215 | no | -$0.21 |
| 100 | 2026-09-09 | B | Will Liverpool FC win on 2026-09-09? | poly:931724 | 0.545 | yes | $0.45 |

## 5. Group A vs Group B

One YES share per market, bought at the recorded 7-day snapshot price and held to resolution (payout $1 if YES won, else $0). Gross, no costs.

|  | Markets | Events | Avg price | Median price | Wins / losses | Win rate | Break-even win rate | Edge (win rate − avg price) | Avg P/L per share | Total P/L | Total paid | ROI |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Group A | 28 | 28 | 0.253 | 0.255 | 13 / 15 | 46.4% | 25.3% | +21.2 pts | $0.21 | $5.93 | $7.07 | 83.9% |
| Group B | 72 | 68 | 0.539 | 0.538 | 44 / 28 | 61.1% | 53.9% | +7.2 pts | $0.07 | $5.17 | $38.83 | 13.3% |
| Combined | 100 | 90 | 0.459 | 0.520 | 57 / 43 | 57.0% | 45.9% | +11.1 pts | $0.11 | $11.10 | $45.90 | 24.2% |

## 6. Combined portfolio

The *Combined* row above is the portfolio of all 100 positions. The equal-dollar version ($1 per market) is in the next table.

## 7. Three cost scenarios

Costs are **illustrative assumptions**, added per share to the entry price: not verified Polymarket fees or historical spreads.

| Scenario |  | One share: total P/L | One share: ROI | One share: break-even win rate | $1 per trade: total P/L | $1 per trade: ROI |
|---|---|---|---|---|---|---|
| A (+$0.00/share) | Group A | $5.93 | 83.9% | 25.3% | $24.47 | 87.4% |
| A (+$0.00/share) | Group B | $5.17 | 13.3% | 53.9% | $10.30 | 14.3% |
| A (+$0.00/share) | Combined | $11.10 | 24.2% | 45.9% | $34.76 | 34.8% |
| B (+$0.01/share) | Group A | $5.65 | 76.9% | 26.3% | $22.41 | 80.0% |
| B (+$0.01/share) | Group B | $4.45 | 11.3% | 54.9% | $8.78 | 12.2% |
| B (+$0.01/share) | Combined | $10.10 | 21.5% | 46.9% | $31.19 | 31.2% |
| C (+$0.02/share) | Group A | $5.37 | 70.4% | 27.3% | $20.51 | 73.3% |
| C (+$0.02/share) | Group B | $3.73 | 9.3% | 55.9% | $7.32 | 10.2% |
| C (+$0.02/share) | Combined | $9.10 | 19.0% | 47.9% | $27.84 | 27.8% |

## 8. Statistical uncertainty

Gross results, 95% intervals from an **event-level bootstrap** (10,000 resamples of whole events with replacement), so markets from the same event are not treated as independent evidence. "Resamples with a profit" is the share of resamples whose total P/L was above zero: a rough guide to stability, not a probability that the strategy works.

|  | Events | Total P/L, 95% interval | One-share ROI, 95% interval | $1-per-trade ROI, 95% interval | Resamples with a profit |
|---|---|---|---|---|---|
| Group A | 28 | $0.81 to $11.06 | 11.2% to 159.1% | 13.0% to 163.3% | 99% |
| Group B | 68 | -$2.85 to $12.91 | -7.1% to 33.9% | -6.1% to 34.7% | 90% |
| Combined | 90 | $1.92 to $19.96 | 4.2% to 44.4% | 9.9% to 60.3% | 99% |

**Event concentration.** 90 events; 8 of them contribute more than one market (the largest has 3). The single most profitable event contributed $0.80 of the $11.10 total; without it the total would be $10.30. Events with the largest effect (one share, gross):

| Event | Markets | Groups | P/L | Example market |
|---|---|---|---|---|
| poly:827176 | 1 | A | $0.80 | Saints vs. Ravens |
| poly:139236 | 1 | A | $0.78 | Will Alexander Zverev win the 2026 Men's US Open? |
| poly:204972 | 1 | A | $0.76 | Will Luiz Inácio Lula da Silva finish in second place in the first round of the 2026 Brazilian presidential election? |
| poly:946150 | 1 | A | $0.76 | Will Real Betis Balompié win on 2026-09-14? |
| poly:827198 | 1 | A | $0.75 | Browns vs. Buccaneers |

## 9. Comparison with an earlier sample

The 100 qualifying markets that closed just before the latest sample (no overlap), closing between 2026-06-27 and 2026-09-08. Same rules, gross.

| Sample |  | Markets | Events | Win rate | Avg price | Edge (win rate − avg price) | Total P/L | ROI |
|---|---|---|---|---|---|---|---|---|
| Latest 100 | Group A | 28 | 28 | 46.4% | 0.253 | +21.2 pts | $5.93 | 83.9% |
| Latest 100 | Group B | 72 | 68 | 61.1% | 0.539 | +7.2 pts | $5.17 | 13.3% |
| Latest 100 | Combined | 100 | 90 | 57.0% | 0.459 | +11.1 pts | $11.10 | 24.2% |
| Earlier 100 | Group A | 29 | 25 | 37.9% | 0.251 | +12.9 pts | $3.73 | 51.3% |
| Earlier 100 | Group B | 71 | 64 | 50.7% | 0.527 | -2.0 pts | -$1.42 | -3.8% |
| Earlier 100 | Combined | 100 | 86 | 47.0% | 0.447 | +2.3 pts | $2.31 | 5.2% |

## 10. Limitations and interpretation

- **Look-ahead bias.** The entry moment is 7 days before the market's *actual* close. Many markets close earlier or later than their scheduled end date, so a trader could not have known when "7 days before close" was. This alone means the results are retrospective, not achievable. Section 13 measures how much of the result this explains.
- **Recorded prices, not executable ones.** The snapshot is a recorded price from the price-history API. Buying at it would have meant paying the ask, plus fees and slippage, which these numbers don't include beyond the illustrative cost scenarios.
- **Liquidity.** Nothing here says how many shares were actually available at those prices.
- **Small sample.** 100 markets from 90 events is small; the intervals in section 8 show how wide the plausible range is.
- **Chosen after looking.** The two intervals came from the calibration chart of the full history, which includes these markets, so this is not an independent test of an idea formed beforehand.

## 11. What a real backtest would need

- The **expected resolution time known at entry** (e.g. the scheduled end date), with entries timed from it instead of from the actual close.
- **Historical executable bid/ask prices** (order-book snapshots), not recorded trade or mid prices.
- **Trading fees and slippage** for realistic order sizes.
- **Available order-book liquidity** at the time of entry, to know what size could have been filled.
- **Chronological out-of-sample validation:** choose the intervals on one period, then test them only on later markets that played no part in choosing them.

## 12. All eligible markets (full history)

The same rules applied to **every** qualifying market in the database: 992 markets from 862 events, closing 2024-03-05 to 2026-10-20. This includes the latest and earlier samples above. With more markets, a lucky streak matters less, so these numbers show whether the latest 100 were typical. The same limitations apply (section 10).

### Performance

One YES share per market, bought at the recorded 7-day snapshot price and held to resolution (payout $1 if YES won, else $0). Gross, no costs.

|  | Markets | Events | Avg price | Median price | Wins / losses | Win rate | Break-even win rate | Edge (win rate − avg price) | Avg P/L per share | Total P/L | Total paid | ROI |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Group A | 372 | 325 | 0.250 | 0.255 | 138 / 234 | 37.1% | 25.0% | +12.1 pts | $0.12 | $44.92 | $93.08 | 48.3% |
| Group B | 620 | 596 | 0.537 | 0.535 | 341 / 279 | 55.0% | 53.7% | +1.3 pts | $0.01 | $7.87 | $333.13 | 2.4% |
| Combined | 992 | 862 | 0.430 | 0.505 | 479 / 513 | 48.3% | 43.0% | +5.3 pts | $0.05 | $52.79 | $426.21 | 12.4% |

### Cost scenarios

| Scenario |  | One share: total P/L | One share: ROI | One share: break-even win rate | $1 per trade: total P/L | $1 per trade: ROI |
|---|---|---|---|---|---|---|
| A (+$0.00/share) | Group A | $44.92 | 48.3% | 25.0% | $178.54 | 48.0% |
| A (+$0.00/share) | Group B | $7.87 | 2.4% | 53.7% | $14.13 | 2.3% |
| A (+$0.00/share) | Combined | $52.79 | 12.4% | 43.0% | $192.66 | 19.4% |
| B (+$0.01/share) | Group A | $41.20 | 42.6% | 26.0% | $157.11 | 42.2% |
| B (+$0.01/share) | Group B | $1.67 | 0.5% | 54.7% | $2.51 | 0.4% |
| B (+$0.01/share) | Combined | $42.87 | 9.8% | 44.0% | $159.63 | 16.1% |
| C (+$0.02/share) | Group A | $37.48 | 37.3% | 27.0% | $137.31 | 36.9% |
| C (+$0.02/share) | Group B | -$4.53 | -1.3% | 55.7% | -$8.68 | -1.4% |
| C (+$0.02/share) | Combined | $32.95 | 7.4% | 45.0% | $128.64 | 13.0% |

### Uncertainty (event-level bootstrap, 10,000 resamples)

|  | Events | Total P/L, 95% interval | One-share ROI, 95% interval | $1-per-trade ROI, 95% interval | Resamples with a profit |
|---|---|---|---|---|---|
| Group A | 325 | $27.33 to $62.49 | 29.6% to 67.2% | 28.9% to 67.2% | 100% |
| Group B | 596 | -$16.81 to $32.07 | -5.1% to 9.6% | -5.2% to 9.6% | 73% |
| Combined | 862 | $22.60 to $82.61 | 5.3% to 19.4% | 10.7% to 28.1% | 100% |

**Event concentration.** 104 of 862 events contribute more than one market (the largest has 8); without the single most profitable event the total would be $51.03 instead of $52.79.

### Latest 100 vs earlier 100 vs all

| Sample |  | Markets | Events | Win rate | Avg price | Edge (win rate − avg price) | Total P/L | ROI |
|---|---|---|---|---|---|---|---|---|
| Latest 100 | Group A | 28 | 28 | 46.4% | 0.253 | +21.2 pts | $5.93 | 83.9% |
| Latest 100 | Group B | 72 | 68 | 61.1% | 0.539 | +7.2 pts | $5.17 | 13.3% |
| Latest 100 | Combined | 100 | 90 | 57.0% | 0.459 | +11.1 pts | $11.10 | 24.2% |
| Earlier 100 | Group A | 29 | 25 | 37.9% | 0.251 | +12.9 pts | $3.73 | 51.3% |
| Earlier 100 | Group B | 71 | 64 | 50.7% | 0.527 | -2.0 pts | -$1.42 | -3.8% |
| Earlier 100 | Combined | 100 | 86 | 47.0% | 0.447 | +2.3 pts | $2.31 | 5.2% |
| All 992 | Group A | 372 | 325 | 37.1% | 0.250 | +12.1 pts | $44.92 | 48.3% |
| All 992 | Group B | 620 | 596 | 55.0% | 0.537 | +1.3 pts | $7.87 | 2.4% |
| All 992 | Combined | 992 | 862 | 48.3% | 0.430 | +5.3 pts | $52.79 | 12.4% |

### Stability over time (half-years, gross)

If an edge is real it should appear in most periods, not just one. Periods with few markets swing a lot by chance.

| Period | Group | Markets | Win rate | Avg price | Edge | Total P/L | ROI |
|---|---|---|---|---|---|---|---|
| 2024 H1 | A | 1 | 100.0% | 0.285 | +71.5 pts | $0.71 | 250.3% |
| 2024 H2 | A | 29 | 31.0% | 0.261 | +4.9 pts | $1.43 | 18.8% |
| 2024 H2 | B | 15 | 53.3% | 0.539 | -0.6 pts | -$0.09 | -1.1% |
| 2025 H1 | A | 38 | 39.5% | 0.252 | +14.3 pts | $5.43 | 56.8% |
| 2025 H1 | B | 49 | 59.2% | 0.539 | +5.3 pts | $2.58 | 9.8% |
| 2025 H2 | A | 53 | 39.6% | 0.248 | +14.8 pts | $7.85 | 59.8% |
| 2025 H2 | B | 155 | 51.0% | 0.533 | -2.3 pts | -$3.60 | -4.4% |
| 2026 H1 | A | 199 | 34.7% | 0.249 | +9.8 pts | $19.51 | 39.4% |
| 2026 H1 | B | 261 | 56.3% | 0.542 | +2.1 pts | $5.52 | 3.9% |
| 2026 H2 | A | 52 | 44.2% | 0.250 | +19.2 pts | $9.98 | 76.7% |
| 2026 H2 | B | 140 | 55.7% | 0.532 | +2.5 pts | $3.46 | 4.6% |

## 13. Removing the look-ahead: scheduled end dates

Every market also has a **scheduled** end date (`endDate`), which a trader knows in advance. The scheduled end dates come from the raw market download of 2026-10-20; 992 of 992 eligible markets have one. Comparing it with the actual close shows where the original edge came from (all eligible markets, original entry rule, one share each, gross):

| Closed | Markets | Win rate | Avg price | Edge | Total P/L | ROI |
|---|---|---|---|---|---|---|
| early | 172 | 59.9% | 0.407 | +19.2 pts | $33.01 | 47.2% |
| on schedule | 751 | 47.1% | 0.443 | +2.8 pts | $21.02 | 6.3% |
| late | 69 | 31.9% | 0.337 | -1.8 pts | -$1.24 | -5.3% |

### (a) Only markets that closed on schedule

The original selection, keeping only markets that closed within 1 day of their scheduled end. For these, "7 days before close" was (almost) knowable in advance.

|  | Markets | Events | Avg price | Median price | Wins / losses | Win rate | Break-even win rate | Edge (win rate − avg price) | Avg P/L per share | Total P/L | Total paid | ROI |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Group A | 245 | 214 | 0.250 | 0.255 | 86 / 159 | 35.1% | 25.0% | +10.1 pts | $0.10 | $24.79 | $61.21 | 40.5% |
| Group B | 506 | 483 | 0.537 | 0.530 | 268 / 238 | 53.0% | 53.7% | -0.7 pts | -$0.01 | -$3.77 | $271.77 | -1.4% |
| Combined | 751 | 652 | 0.443 | 0.510 | 354 / 397 | 47.1% | 44.3% | +2.8 pts | $0.03 | $21.02 | $332.98 | 6.3% |

|  | Events | Total P/L, 95% interval | One-share ROI, 95% interval | $1-per-trade ROI, 95% interval | Resamples with a profit |
|---|---|---|---|---|---|
| Group A | 214 | $10.63 to $39.69 | 17.3% to 64.5% | 15.8% to 63.9% | 100% |
| Group B | 483 | -$25.95 to $19.14 | -9.6% to 7.0% | -9.6% to 7.1% | 38% |
| Combined | 652 | -$5.08 to $46.91 | -1.5% to 14.1% | 2.4% to 21.4% | 94% |

### (b) Entry 7 days before the scheduled end

A stricter test that needs no filtering: for every market, the entry price is taken 7 days before the **scheduled** end with the same snapshot rule (latest price at or before that moment, at most 1 day old), and only if the market was still open then. Markets that resolved early after entry are kept, as a trader would have held them. Then the same two price intervals are applied: 1151 markets qualify.

|  | Markets | Events | Avg price | Median price | Wins / losses | Win rate | Break-even win rate | Edge (win rate − avg price) | Avg P/L per share | Total P/L | Total paid | ROI |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Group A | 390 | 356 | 0.250 | 0.255 | 130 / 260 | 33.3% | 25.0% | +8.3 pts | $0.08 | $32.46 | $97.54 | 33.3% |
| Group B | 761 | 736 | 0.542 | 0.535 | 399 / 362 | 52.4% | 54.2% | -1.7 pts | -$0.02 | -$13.27 | $412.27 | -3.2% |
| Combined | 1151 | 1039 | 0.443 | 0.515 | 529 / 622 | 46.0% | 44.3% | +1.7 pts | $0.02 | $19.19 | $509.81 | 3.8% |

|  | Events | Total P/L, 95% interval | One-share ROI, 95% interval | $1-per-trade ROI, 95% interval | Resamples with a profit |
|---|---|---|---|---|---|
| Group A | 356 | $14.51 to $50.95 | 14.9% to 52.4% | 14.9% to 52.8% | 100% |
| Group B | 736 | -$39.86 to $14.17 | -9.6% to 3.5% | -9.5% to 3.6% | 18% |
| Combined | 1039 | -$13.12 to $51.28 | -2.6% to 10.1% | 1.8% to 17.1% | 88% |

| Scenario |  | One share: total P/L | One share: ROI | One share: break-even win rate | $1 per trade: total P/L | $1 per trade: ROI |
|---|---|---|---|---|---|---|
| A (+$0.00/share) | Group A | $32.46 | 33.3% | 25.0% | $131.13 | 33.6% |
| A (+$0.00/share) | Group B | -$13.27 | -3.2% | 54.2% | -$23.65 | -3.1% |
| A (+$0.00/share) | Combined | $19.19 | 3.8% | 44.3% | $107.48 | 9.3% |
| B (+$0.01/share) | Group A | $28.56 | 28.2% | 26.0% | $110.77 | 28.4% |
| B (+$0.01/share) | Group B | -$20.88 | -5.0% | 55.2% | -$37.07 | -4.9% |
| B (+$0.01/share) | Combined | $7.68 | 1.5% | 45.3% | $73.71 | 6.4% |
| C (+$0.02/share) | Group A | $24.66 | 23.4% | 27.0% | $91.96 | 23.6% |
| C (+$0.02/share) | Group B | -$28.49 | -6.7% | 56.2% | -$50.00 | -6.6% |
| C (+$0.02/share) | Combined | -$3.83 | -0.7% | 46.3% | $41.96 | 3.6% |

### Caveats for this section

- The scheduled end dates come from the latest raw download. Polymarket can change a market's end date while it trades, so the date a trader saw at entry may have been different.
- Prices are still recorded prices, not executable quotes, and the intervals were still chosen after looking at the full history (section 10).
- Many of these markets are single sports games, so a result here may describe sports markets more than prediction markets in general.
