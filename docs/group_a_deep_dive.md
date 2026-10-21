# Group A deep dive: buying YES at 20–30%

*Generated 2026-10-21 20:28 UTC by `src/analysis/group_a_deep_dive.py` from the local database (read-only) and the raw market download of 2026-10-20. Every number is computed from recorded prices and outcomes.*

> **Retrospective and hypothetical.** Entries use recorded prices, not executable bid/ask quotes, and the 20–30% interval was chosen after looking at this history. This explores *where* the historical pattern appears; it does not show that it could have been traded. See `docs/combined_yes_strategy.md` for the full methodology and limitations.

## Summary

- **All Group A (7 days before):** ROI 33.3% over 390 markets (95% interval 14.9% to 52.2%, above zero); positive in exploration (26.4%, 320 markets) and positive in test (64.5%, 70 markets).
- **Single game or match:** ROI 28.8% over 279 markets (95% interval 6.9% to 51.0%, above zero); positive in exploration (23.1%, 222 markets) and positive in test (50.8%, 57 markets).
- **Other:** ROI 45.0% over 111 markets (95% interval 9.6% to 81.3%, above zero); positive in exploration (34.1%, 98 markets) and positive in test (130.6%, 13 markets).
- **Higher volume (> $2.06M):** ROI 25.1% over 195 markets (95% interval -0.1% to 50.6%, includes zero); positive in exploration (14.9%, 170 markets) and positive in test (93.8%, 25 markets).
- **Lower volume (≤ $2.06M):** ROI 41.2% over 195 markets (95% interval 15.1% to 67.8%, above zero); positive in exploration (38.9%, 150 markets) and positive in test (48.7%, 45 markets).
- **3 days before:** ROI 25.9% over 563 markets (95% interval 10.6% to 41.2%, above zero); positive in exploration (24.0%, 464 markets) and positive in test (34.3%, 99 markets).
- **14 days before:** ROI 21.3% over 264 markets (95% interval -1.0% to 44.0%, includes zero); positive in exploration (24.5%, 229 markets) and positive in test (1.1%, 35 markets).

A segment is more convincing when its ROI is positive in **both** the exploration and the test period and its interval for all markets stays above zero. Small segments swing a lot by chance.

## Definitions

Fixed before computing any result in this report:

| Question | Definition |
|---|---|
| Entry | The **knowable entry**: the price 7 days before each market's *scheduled* end (latest price at or before that moment, at most 1 day old), only if the market was still open. Group A = YES price in [0.20, 0.30). One share per market, held to resolution. |
| 1. Sports | **Single game or match** if the question contains `vs`, `win on <date>`, a point spread like `(-1.5)`, or `O/U`; otherwise **other** (politics, crypto, "by date" events, season-long sports futures). The rule was written from question wording only, without looking at prices or outcomes. |
| 2. Liquidity | Total traded volume, split at the **median** of the Group A markets into lower and higher volume. Volume is the market's final total, not the volume available at entry. |
| 3. Timing | The same rules with entry 3, 7, 14 days before the scheduled end. A real effect should look similar at nearby entry times. |
| Hold-out | **Exploration:** markets that closed before 2026-07-01. **Test:** closed on or after it. Each result is shown for both, and for all markets together. |
| Uncertainty | 95% intervals from an event-level bootstrap (5,000 resamples of whole events). ROI is also shown with +$0.02 per share of illustrative costs. |

## 1. Is it a sports effect?

Examples of each type: single game or match: *Commanders vs. 49ers*; *Almaty Open: Mikhail Kukushkin vs Stefanos Sakellaridis*; *Chargers vs. Chiefs*. Other: *Will Luiz Inácio Lula da Silva finish in second place in the first round of the 2026 Brazilian presidential election?*; *Will Flavio Bolsonaro win the most votes in the first round of the 2026 Brazil presidential election?*; *Trump renames AI by September 30?*.

| Segment | Period | Markets | Events | Win rate | Avg price | Edge | ROI | ROI 95% interval | ROI +$0.02/share |
|---|---|---|---|---|---|---|---|---|---|
| Single game or match | exploration | 222 | 197 | 31.1% | 0.252 | +5.8 pts | 23.1% | -0.3% to 47.9% | 14.1% |
| Single game or match | test | 57 | 57 | 38.6% | 0.256 | +13.0 pts | 50.8% | 1.5% to 100.5% | 39.8% |
| Single game or match | all | 279 | 254 | 32.6% | 0.253 | +7.3 pts | 28.8% | 6.9% to 51.0% | 19.4% |
| Other | exploration | 98 | 90 | 32.7% | 0.244 | +8.3 pts | 34.1% | -2.4% to 73.7% | 23.9% |
| Other | test | 13 | 12 | 53.8% | 0.234 | +30.5 pts | 130.6% | 14.9% to 236.5% | 112.4% |
| Other | all | 111 | 102 | 35.1% | 0.242 | +10.9 pts | 45.0% | 9.6% to 81.3% | 33.9% |

## 2. Liquidity: lower vs higher volume

Split at the median total volume of these Group A markets ($2.06M). If the edge only appears in lower-volume markets, it is more likely to vanish once spreads and price impact are paid.

| Segment | Period | Markets | Events | Win rate | Avg price | Edge | ROI | ROI 95% interval | ROI +$0.02/share |
|---|---|---|---|---|---|---|---|---|---|
| Higher volume (> $2.06M) | exploration | 170 | 150 | 28.2% | 0.246 | +3.7 pts | 14.9% | -11.3% to 40.6% | 6.3% |
| Higher volume (> $2.06M) | test | 25 | 24 | 48.0% | 0.248 | +23.2 pts | 93.8% | 15.1% to 172.3% | 79.3% |
| Higher volume (> $2.06M) | all | 195 | 174 | 30.8% | 0.246 | +6.2 pts | 25.1% | -0.1% to 50.6% | 15.7% |
| Lower volume (≤ $2.06M) | exploration | 150 | 146 | 35.3% | 0.254 | +9.9 pts | 38.9% | 9.5% to 68.1% | 28.8% |
| Lower volume (≤ $2.06M) | test | 45 | 45 | 37.8% | 0.254 | +12.4 pts | 48.7% | -5.8% to 105.1% | 37.8% |
| Lower volume (≤ $2.06M) | all | 195 | 191 | 35.9% | 0.254 | +10.5 pts | 41.2% | 15.1% to 67.8% | 30.9% |

## 3. Timing: 3, 7 and 14 days before the scheduled end

Each row uses a different entry moment, so the set of markets in Group A changes too (a market priced 25% at 14 days may be 40% at 3 days). A real effect should not depend on one exact entry day.

| Segment | Period | Markets | Events | Win rate | Avg price | Edge | ROI | ROI 95% interval | ROI +$0.02/share |
|---|---|---|---|---|---|---|---|---|---|
| 3 days before | exploration | 464 | 426 | 30.8% | 0.248 | +6.0 pts | 24.0% | 7.0% to 41.1% | 14.8% |
| 3 days before | test | 99 | 88 | 34.3% | 0.256 | +8.8 pts | 34.3% | -0.0% to 71.7% | 24.6% |
| 3 days before | all | 563 | 514 | 31.4% | 0.250 | +6.5 pts | 25.9% | 10.6% to 41.2% | 16.6% |
| 7 days before | exploration | 320 | 287 | 31.6% | 0.250 | +6.6 pts | 26.4% | 5.8% to 45.8% | 17.0% |
| 7 days before | test | 70 | 69 | 41.4% | 0.252 | +16.2 pts | 64.5% | 19.5% to 111.0% | 52.4% |
| 7 days before | all | 390 | 356 | 33.3% | 0.250 | +8.3 pts | 33.3% | 14.9% to 52.2% | 23.4% |
| 14 days before | exploration | 229 | 197 | 31.0% | 0.249 | +6.1 pts | 24.5% | -0.1% to 49.8% | 15.2% |
| 14 days before | test | 35 | 34 | 25.7% | 0.254 | +0.3 pts | 1.1% | -53.5% to 59.9% | -6.2% |
| 14 days before | all | 264 | 231 | 30.3% | 0.250 | +5.3 pts | 21.3% | -1.0% to 44.0% | 12.3% |

## Limitations

- Recorded prices, not executable quotes; no spread, fees or liquidity beyond the illustrative +$0.02.
- The 20–30% interval comes from the full history's calibration chart, so even the test period is not a fully independent test of that choice.
- Volume is each market's final total volume, which includes trading after entry.
- The sports rule is a text heuristic; a few markets will be misclassified.
- Scheduled end dates come from the latest raw download and may have changed while a market traded.
