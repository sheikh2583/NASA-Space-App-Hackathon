# T54 interval diagnosis

**Run:** `T54-interval-diagnosis-20261115` (read-only; no fit or calibration write). The complete run record and traced sample IDs are in the ignored `results/runs/T54-interval-diagnosis-20261115/` directory.

## Inputs and integrity

The diagnostic used the protected T51 processed baseline. SHA256 values below identify the exact saved inputs:

| Artifact | SHA256 |
|---|---|
| `predictions_oof.parquet` | `e1c258c7e27febbeb53684d0f456374d854ea5c4c43b59acaee8442b01d45a4a` |
| `grid_daily.parquet` | `af9f051c4b30fb31bdaf2aca4a95b75f649d55f934ad1d2645114d8cccfa8b5c` |
| `cells.parquet` | `f0c1490488c6b9e2d14e082413f880edd9d040bb842f0bb068d365315abdaee4` |
| `interval_table.json` | `2a5e18dd319e663ab65f7b78f914f121f0acf41f74aa31b634aada84d6626e4b` |

The interval table SHA256 was identical before and after the diagnostic. The exact environment, source revision and source-file hashes, commands, fixed seeds, row-level holdout metrics, and rectangle draw cell IDs are in `diagnosis.json` and `rectangle-draws.jsonl` under the ignored run directory.

## Target and coverage audit

`daily_oof` reconciled daily SNPP detections to every held-out weekly truth row without a mismatch. OOF keys were unique; daily contributor keys were also unique. The monthly target is `calendar_month_daily_allocation`: 121,171 cell-day rows across the two combinations, with 111 complete calendar months from May 2012 through September 2026. Terra+Aqua contributes 62,996 daily rows and Terra-only 58,175. The diagnostic did not substitute Monday-week buckets.

The test split contains 211 unique cells in 5 spatial blocks, 716 weeks and 15 calendar years for each input combination. Neither this block sample nor the <=400-cell rectangle draws cover complete named AOIs: all 11 saved areas remain `insufficient_support`. Among the more-covered areas, Punjab has 11 of 537 test cells, Sylhet 36 of 145, and Chittagong 164 of 365; none is a complete out-of-sample area.

For each resolution and input combination, the interval report used 4,000 seeded rectangle draws. A separate 400-draw overlap audit retained contributing cell IDs. It found repeated cell-period samples (maximum Jaccard overlap 1.0 in each group); draws share the same 5 blocks and 15 years and cannot be treated as independent physical observations. The complete overlap statistics are recorded in the run JSON.

The traced sample also records 80 resolution/combo/stratum summaries across overall, positive and zero activity, positive truth with zero prediction, area size, magnitude, season, and land cover. Each summary has a denominator, unique cells, blocks, periods and years, miss tails, width, score, contributing-cell count, and drawn rectangle size. These 400-draw support summaries complement the 4,000-draw interval report and are not mislabeled as the same sample.

## Measured conditional failures

Ten reported groups with at least 500 draws had coverage below the registered 0.80 stop threshold:

| Resolution / input | Group | n | Coverage | Miss above upper | Mean width | Interval score |
|---|---|---:|---:|---:|---:|---:|
| Week / Terra+Aqua | Positive truth | 1,100 | 0.733 | 0.267 | 203.82 | 248.26 |
| Week / Terra-only | Positive truth | 1,095 | 0.471 | 0.529 | 94.17 | 326.66 |
| Week / Terra-only | Zero prediction, positive truth | 814 | 0.342 | 0.658 | 1.67 | 184.55 |
| Week / Terra-only | Season q2 | 1,065 | 0.775 | 0.225 | 24.97 | 155.58 |
| Month / Terra+Aqua | Zero prediction, positive truth | 678 | 0.568 | 0.432 | 2.60 | 37.14 |
| Month / Terra-only | Positive truth | 1,784 | 0.603 | 0.378 | 355.98 | 618.04 |
| Month / Terra-only | Zero prediction, positive truth | 1,070 | 0.423 | 0.577 | 2.80 | 228.34 |
| Month / Terra-only | Area size 11–50 | 1,097 | 0.763 | 0.224 | 259.90 | 482.97 |
| Month / Terra-only | Season q1 | 1,050 | 0.744 | 0.211 | 127.73 | 339.77 |
| Month / Terra-only | Season q2 | 1,100 | 0.786 | 0.214 | 385.37 | 550.78 |

The point summaries show the combination gap: in test cell-weeks mean truth is 1.082 for both combinations; mean prediction is 1.423 for Terra+Aqua and 0.726 for Terra-only. Positive-truth rows with zero predictions number 9,323 of 16,772 for Terra+Aqua and 14,886 of 16,772 for Terra-only. These are conditional association diagnostics, not a cause of fire activity.

## Ranked mechanisms for T55

1. **Input-combination mismatch (strongest implementation evidence).** The current table entries have no satellite-combination key; `build_table` calls `_aggregate_samples` with its Terra+Aqua default, and `interval_report` evaluates that same table for Terra-only. The lower Terra-only predictions and much lower positive-truth coverage match this mismatch. T55 will compare a separately calibrated table by combination, fit only on calibration OOF rows.
2. **Near-zero upper-tail undercoverage.** Zero predictions with positive truth have coverages 0.342–0.568 and mean widths 1.67–2.80. This supports screening a prediction-time zero-prediction additive fallback calibrated from calibration rows. Truth-positive membership remains a diagnostic group and cannot select a deployed band.

These are hypotheses about the implementation and interval behavior, not causal attribution. T55 must preserve both failure rows and all holdouts. T53’s block/year support and bootstrap acceptance gates remain binding; a candidate may still finish limited or unsupported.
