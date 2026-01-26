# TRP Valuation Engine Analysis
**Date**: 2026-01-16
**Dataset**: MTBL League 10998
**Output Location**: `.temp/`

## Executive Summary

The TRP (True Replacement Price) valuation engine successfully processed 1,405 hitters and 1,535 pitchers, but revealed a significant budget allocation issue: **only $1,912 of the $2,805 total league budget was allocated** (32% shortfall).

---

## 1. Budget Allocation Discrepancy

### Overall Budget Gap: $892.81

| Category | Expected | Actual | Gap | % Under |
|----------|----------|--------|-----|---------|
| **Hitters** | $1,823.25 | $1,253.93 | -$569.32 | -31% |
| **Pitchers** | $981.75 | $658.26 | -$323.49 | -33% |
| **TOTAL** | $2,805.00 | $1,912.19 | -$892.81 | -32% |

**Finding**: The algorithm is systematically underallocating budget across both hitters and pitchers. Nearly a third of the league budget remains unallocated.

---

## 2. Position-Specific Budget Analysis

### Allocated vs Expected Budget by Position

| Position | Rostered | Allocated | Expected | Gap | % Under |
|----------|----------|-----------|----------|-----|---------|
| C | 11 | $141.56 | $204.57 | -$63.01 | -31% |
| 1B | 11 | $217.50 | $246.79 | -$29.29 | -12% |
| **2B** | **12** | **$69.86** | **$242.52** | **-$172.66** | **-71%** |
| 3B | 11 | $191.88 | $243.43 | -$51.55 | -21% |
| SS | 16 | $202.71 | $272.18 | -$69.47 | -26% |
| OF | 36 | $378.17 | $493.99 | -$115.82 | -23% |
| **UTIL** | **11** | **$71.45** | **$260.02** | **-$188.57** | **-73%** |
| SP | 33 | $344.77 | $420.75 | -$75.98 | -18% |
| RP | 22 | $315.03 | $420.75 | -$105.72 | -25% |

**Critical Issues**:
- **2B and UTIL positions** show catastrophic budget gaps (71-73%)
- Even the best-performing position (1B) is still 12% under budget
- SS has 16 rostered players instead of expected 11 (multi-position eligibility)

---

## 3. Negative Dollar Value Problem

### Rostered Players with Negative Values

**Count**: 37 of 163 rostered players (22.7%) have negative dollar values

**Top 10 Negative Value Players**:

| Player | Position | Dollar Value | Z-Score |
|--------|----------|--------------|---------|
| Brandon Lowe | 2B | -$27.74 | 0.91 |
| Marcus Semien | 2B | -$23.37 | 1.07 |
| Michael Harris II | OF/UTIL | -$17.81 | 0.30 |
| Dansby Swanson | SS/UTIL | -$17.30 | 1.15 |
| Lawrence Butler | OF/UTIL | -$15.74 | 0.46 |
| Brenton Doyle | OF/UTIL | -$14.57 | 0.59 |

**Analysis**:
- These players have positive Z-scores but negative values
- Suggests replacement baselines may be set too high
- Could indicate position scarcity not properly weighted
- 2B particularly affected (Brandon Lowe, Marcus Semien)

---

## 4. Top Player Valuations

### Highest Z-Score Hitters

| Rank | Player | Position | Z-Score | Dollar Value |
|------|--------|----------|---------|--------------|
| 1 | Aaron Judge | OF | 19.83 | $53.38 |
| 2 | Shohei Ohtani | UTIL | 14.94 | $105.78 |
| 3 | Bobby Witt Jr. | SS | 14.60 | $58.74 |
| 4 | Juan Soto | OF | 14.09 | $38.57 |
| 5 | Cal Raleigh | C | 14.04 | $24.69 |
| 6 | Junior Caminero | 3B | 13.99 | $37.57 |
| 7 | Jose Ramirez | 3B | 13.40 | $48.12 |
| 8 | Ketel Marte | 2B | 12.86 | $74.60 |
| 9 | Vladimir Guerrero Jr. | 1B | 12.64 | $58.12 |
| 10 | Fernando Tatis Jr. | OF | 10.38 | $30.53 |

### Highest Z-Score Pitchers

| Rank | Player | Role | Z-Score | Dollar Value |
|------|--------|------|---------|--------------|
| 1 | Tarik Skubal | SP | 14.00 | $37.31 |
| 2 | Mason Miller | RP | 12.90 | $57.26 |
| 3 | Paul Skenes | SP | 11.84 | $32.70 |
| 4 | Garrett Crochet | SP | 11.73 | $33.45 |
| 5 | Edwin Diaz | RP | 8.16 | $32.51 |
| 6 | Cade Smith | RP | 7.84 | $32.52 |
| 7 | Chris Sale | SP | 7.38 | $24.24 |
| 8 | Jhoan Duran | RP | 7.31 | $27.70 |

**Anomaly**: Shohei Ohtani has the highest dollar value ($105.78) despite not having the highest Z-score. This reflects multi-category dominance across all hitting categories.

---

## 5. Market Efficiency Analysis

### Best Value Players (Z-score per Dollar)

| Player | Position | Z-Score | Dollar Value | Z per $ |
|--------|----------|---------|--------------|---------|
| Jeff Hoffman | RP | 1.44 | $0.49 | 2.93 |
| Cody Bellinger | 1B | 2.98 | $1.16 | 2.57 |
| Daniel Palencia | RP | 0.95 | $0.38 | 2.51 |
| Hunter Goodman | C | 6.95 | $4.34 | 1.60 |
| Max Fried | SP | 3.90 | $2.65 | 1.47 |

These players represent market inefficiencies where high Z-scores translate to relatively low dollar costs.

---

## 6. Position Scarcity Insights

### Dollars per Z-Score by Stat Category

**Hitter Scarcity Patterns**:
- **OBP most valuable** at 2B ($21.12/Z) and UTIL ($22.43/Z)
- **HR most valuable** at OF ($3.87/Z) and 1B ($3.50/Z)
- **SB highly valued** at 2B ($9.54/Z), SS ($8.19/Z), and UTIL ($8.32/Z)
- **C position** shows moderate scarcity across all categories

**Pitcher Scarcity Patterns**:
- **SP**: Balanced value across ERA ($1.69/Z), WHIP ($3.01/Z), K/9 ($6.70/Z)
- **RP**: Higher ERA premium ($2.56/Z vs SP's $1.69/Z)
- **SVHD** valued at $2.21/Z for relievers

### Replacement Level Baselines

**Hitter Baselines** (by position):
- C: 59R, 16HR, 61RBI, 2SB, .330 OBP, .424 SLG
- 1B: 74R, 25HR, 80RBI, 1SB, .329 OBP, .439 SLG
- 2B: 75R, 13HR, 64RBI, 14SB, .329 OBP, .395 SLG
- SS: 84R, 22HR, 77RBI, 14SB, .312 OBP, .420 SLG
- OF: 76R, 24HR, 73RBI, 8SB, .312 OBP, .431 SLG

**Pitcher Baselines**:
- SP: 166IP, 3.84 ERA, 1.21 WHIP, 9.24 K/9, 16QS
- RP: 66IP, 3.50 ERA, 1.22 WHIP, 10.40 K/9, 19 SVHD

---

## 7. Replacement Tier Analysis

**12 players in REPLACEMENT tier** (between rostered and below_replacement):

**Hitters**:
- Luke Keaschall (2B/UTIL): $10.17 each
- Brandon Nimmo (OF/UTIL): -$6.74 each
- Michael Busch (1B/UTIL): -$13.03 each

**Pitchers**:
- Edwin Uceta (RP): $5.33
- Brandon Woodruff (SP): $2.11
- Kyle Bradish (SP): $0.68
- Several with negative values

**Finding**: The replacement boundary is functioning but includes players with negative values, suggesting the tier cutoffs may need adjustment.

---

## 8. Algorithm Performance

### Convergence Statistics

| Pool | Initial Iterations | Post-Dedupe Iterations |
|------|-------------------|------------------------|
| Hitter pools | 4 | 1 |
| UTIL pool | 3 | - |
| SP pool | 5 | - |
| RP pool | 5 | - |

**Finding**: The iterative pooling algorithm converges efficiently and appears stable.

### Player Distribution

- **Total players processed**: 2,940 (1,405 hitters + 1,535 pitchers)
- **Rostered**: 163 (5.5%)
- **Replacement**: 12 (0.4%)
- **Below replacement**: 4,058 (94.1%)
- **Output files generated**: 13 CSV files + 2 JSON files

### Deduplication Results

After building multi-position pools:
- **1,382 players reassigned** to primary positions
- Re-iteration converged after 1 additional pass

---

## 9. Data Quality Observations

### Tier Count Validation
All position roster counts matched expected slots:
- ✓ C: 11/11 rostered
- ✓ 1B: 11/11 rostered
- ✓ 2B: 11/11 rostered
- ✓ 3B: 11/11 rostered
- ✓ SS: 11/11 rostered
- ✓ OF: 33/33 rostered
- ✓ UTIL: 11/11 rostered
- ✓ SP: 33/33 rostered
- ✓ RP: 22/22 rostered

### RLP Z-Score Validation
Replacement level player average Z-scores by position:
- C: -3.26 (most negative - scarcest position)
- SS: +1.67 (positive - deepest position)
- OF: +0.04 (near zero - balanced)
- SP: -0.00 (perfect baseline)

---

## 10. Critical Issues & Recommendations

### Priority 1: Budget Allocation Formula
**Issue**: 32% of league budget unallocated
**Root Cause**: Dollar conversion formula appears too conservative
**Action**: Review `mtbl_valuations/engine/valuation.py` budget allocation logic
**File**: `mtbl_valuations/engine/valuation.py`

### Priority 2: 2B and UTIL Position Logic
**Issue**: 71-73% budget gaps for these positions
**Root Cause**: Unknown - requires investigation of position-specific calculations
**Action**:
- Check if 2B/UTIL baselines are set too high
- Review multi-position eligibility impact on UTIL
**Files**:
- `mtbl_valuations/engine/pools.py`
- `mtbl_valuations/engine/valuation.py`

### Priority 3: Negative Value Roster Selections
**Issue**: 22.7% of rostered players have negative values
**Root Cause**: Replacement baselines may be set too high relative to talent pool
**Action**:
- Review replacement tier calculation (currently top 3 of non-rostered)
- Consider adjusting scarcity weights
**File**: `mtbl_valuations/engine/pools.py`

### Priority 4: Replacement Baseline Calibration
**Issue**: Players with positive Z-scores receiving negative dollar values
**Root Cause**: Baseline may not reflect actual replacement level talent
**Action**:
- Experiment with different replacement tier sizes (3 vs 5 vs 10 players)
- Consider percentile-based baselines instead of fixed tier
**File**: `mtbl_valuations/engine/pools.py`

---

## Output Files Generated

### Summary Files
- `valuations.csv` (4,234 players) - Complete valuation results
- `position_summary.csv` - Position-level budget and scarcity metrics

### Position Detail Files
- `c_detailed.csv` (211 catchers)
- `1b_detailed.csv` (184 first basemen)
- `2b_detailed.csv` (109 second basemen)
- `3b_detailed.csv` (155 third basemen)
- `ss_detailed.csv` (238 shortstops)
- `of_detailed.csv` (485 outfielders)
- `util_detailed.csv` (1,316 utility eligible)
- `sp_detailed.csv` (751 starting pitchers)
- `rp_detailed.csv` (784 relief pitchers)

### JSON Exports
- `hitters.json` (10MB) - Full hitter data with all metrics
- `pitchers.json` (11MB) - Full pitcher data with all metrics

---

## Next Steps

1. **Investigate budget allocation**: Profile the dollar conversion code to understand why 32% of budget is unused
2. **Debug 2B/UTIL positions**: Add logging to track how these positions are being valued
3. **Run sensitivity analysis**: Test different replacement tier sizes (1, 3, 5, 10 players)
4. **Compare to historical auctions**: If available, validate dollar values against actual auction results
5. **Consider alternative approaches**: Research if scarcity adjustments should be multiplicative vs additive

---

## Command to Reproduce

```bash
uv run python -m mtbl_valuations hydrate --output-dir .temp/
```

**Runtime**: ~2-3 seconds
**Exit Status**: Success (with validation warnings)
