# Finding True Value in Fantasy Baseball

## Part 5: The Implementation Blueprint

*TRP: True Value. Market Pricing. No Noise.*

---

This part translates all of TRP (True Replacement Price) theory into a buildable system.

What follows is a language-agnostic architectural blueprint. The data structures, core functions, and control flow are specified precisely enough that you could hand this document to an LLM and get a working implementation in one shot.

---

### Inputs

TRP consumes **three upstream artifacts** produced by your ETL. The *engine* itself should not care where these came from (ESPN, FanGraphs, etc.). It just expects consistent, normalized records.

**1. Batters File** (`batters_matched.json`)

An array of batter objects. Identity fields are shared across the whole system (used to build `Player`), while hitter valuation uses the nested stat payloads.

Key identity fields (shared `Player`):

```json
{
  "id_espn": 32801,
  "id_fangraphs": "13510",
  "id_xmlbam": 608070,
  "name": "Jose Ramirez",
  "pro_team": "CLE",
  "primary_position": "3B",
  "eligible_slots": ["3B","1B/3B","IF","DH","UTIL"],
  "status": "active",
  "injury_status": "ACTIVE",
  "active": true,
  "injured": false,
  "fantasy_team": 31,
  "draft_value": 57.0
}
```

Hitter stats are sourced from:

- **Projections (FanGraphs):** `stats.projections` (this is the committed projection source)
- **Actuals (ESPN season totals):** `stats.current_season`

Engine-facing `HitterStats` (normalized):

```
pa   := stats.projections.PA
ab   := stats.projections.AB
r    := stats.projections.R
hr   := stats.projections.HR
rbi  := stats.projections.RBI
obp  := stats.projections.OBP
slg  := stats.projections.SLG
sbn  := stats.projections.SBN         // league category is Net SB
wrc_plus := stats.projections["wRC+"] // optional sort/diagnostic
```

Actuals mirror the same shape, pulled from `stats.current_season`:

```
pa  := stats.current_season.PA
ab  := stats.current_season.AB
r   := stats.current_season.R
hr  := stats.current_season.HR
rbi := stats.current_season.RBI
obp := stats.current_season.OBP
slg := stats.current_season.SLG
sbn := stats.current_season.SBN       // if missing, compute SB - CS
```

**2. Pitchers File** (`pitchers_matched.json`)

An array of pitcher objects with the same identity envelope, plus pitching stat payloads.

Pitcher role:

- `role := primary_position` where `primary_position ∈ {"SP","RP"}`
- If a player has both hitter + pitcher eligibility (e.g., eligible slots include DH/UTIL and P/SP), **role is still driven by `primary_position`** for the pitcher feed. (The hitter feed will carry its own record for the same identity.)

Pitcher stats are sourced from:

- **Projections (FanGraphs):** `stats.projections`
- **Actuals (ESPN season totals):** `stats.current_season`

Engine-facing `PitcherStats` (normalized; OUTS is the preferred innings representation):

Projections mapping (FanGraphs `stats.projections`):

```
outs := stats.projections.IP * 3  // if not provided

era  := stats.projections.ERA
whip := stats.projections.WHIP
k9   := stats.projections["K/9"]
qs   := stats.projections.QS

// Relievers and starters both include these keys; SVHD is also provided directly.
// To be robust, compute SVHD if it is missing/null and the primary position is RP.
svhd := stats.projections.SVHD ?? (stats.projections.SV + stats.projections.HLD)

fip  := stats.projections.FIP          // optional sort/diagnostic
```

Actuals mapping (ESPN `stats.current_season`):

```
// ESPN provides OUTS directly, plus sometimes IP in base-3 tenths.
// OUTS is the canonical value. Ignore IP formatting issues.
outs := stats.current_season.OUTS

era  := stats.current_season.ERA
whip := stats.current_season.WHIP
qs   := stats.current_season.QS

// SVHD: relievers will have SV + HLD populated. Starters should evaluate to 0.
// most payload have SVHD already computed
svhd := (stats.current_season.SV ?? 0) + (stats.current_season.HLD ?? 0)
```


**3. League Summary File** (`league_10998_summary.json`)

This file defines roster economics and scoring categories. TRP uses it to choose the category set (including reversals like ERA/WHIP) and determine league size + budgets.

Relevant fields (example):
The league rules specify outs but the actual stat is IP so whenever we bucket categories, that's what we're looking for. All values should be normalized to IP.
```json
{
  "seasonId": 2025,
  "id": 10998,
  "teams": 11,
  "auctionBudget": 260,
  "acquisitionBudget": 150,
  "scoring": {
    "batting": ["R","RBI","HR","SBN","OBP","SLG"],
    "pitching": ["ERA","WHIP","QS","K/9","OUTS","SVHD"],
    "reverse": ["ERA","WHIP"]
  }
}
```
### Implementation Notes (ETL-Friendly Contracts + Suggested Imports)

This blueprint is language-agnostic, but the pipeline is real, so the **input contracts cannot be hand-wavy**.

At minimum, the loader layer should expose these two typed streams into the valuation engine (already normalized to league categories):
For this project, the resources are always stored in /Users/Shared/BaseballHQ/resources/transform/

- `Iterable[HitterPlayer]` where `HitterPlayer.stats` is sourced from `batters_merged.json`
- `Iterable[PitcherPlayer]` where `PitcherPlayer.stats` is sourced from `pitchers_merged.json`

If you are implementing in Python, the following imports cover the core needs of this document (JSON IO, typing, math, numeric ops):

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional, Iterable, Sequence, Union

import json
import math

import numpy as np
import pandas as pd
```

Loader/adapters should be responsible for upstream quirks (FanGraphs column naming like `wRC+`, ESPN naming like `OUTS`, and category derivations like `K/9` and `SVHD`) and normalize into the engine contracts above.

### Outputs

The pipeline runs once per **valuation source** (`preseason`, `updated`,
`ros`, `synthetic`, `current`) and writes two kinds of artifacts:

```
<output-dir>/
├── hitters.json                 // merged, all 5 sources
├── pitchers.json                // merged, all 5 sources
└── <source>/
    └── position_summary.csv     // pool-level aggregates per source
```

Earlier iterations wrote per-source `valuations.csv`,
`<pos>_detailed.csv`, and per-source `hitters.json` / `pitchers.json`.
Those have been removed: every per-player field they exposed is already
in the merged top-level JSON, none of them carried the Savant pct_rnk
enrichments (which only exist on the merged JSON by design), and the
duplication ate ~175 MB per run. The current layout is a strict
information-preserving simplification.

**1. Merged Hitters / Pitchers JSON** (`hitters.json`, `pitchers.json`)

The canonical per-player output. Each record matches the upstream
`batters_matched.json` / `pitchers_matched.json` schema with two
enrichments:

- **`stats.savant.*` percentile ranks**: every numeric Savant field
  picks up a `<field>_pct_rnk` against the **current**-source settled
  rostered + replacement-level universe. Direction-aware — `K_pct`,
  `swing_miss_pct`, ERA, WHIP, xwOBA-against, etc. are inverted so
  `pct_rnk` always means *good performance for the role* regardless of
  stat polarity.

- **`valuations` nested by source label**: `{preseason, updated, ros,
  synthetic, current}`. Each source-block carries:

```
{
  primary_position: string            // "SS" | "1B" | ... | "UTIL" | "SP" | "RP"
  tier: string                        // "ROSTERED" | "REPLACEMENT" | "BELOW_REPLACEMENT"
  total_z: float                      // sum of settled per-cat z-scores
  total_dollars: float                // sum of per-cat dollar values
  z_scores: { <cat>: float, ... }     // settled z per category (>= 0 after baseline shift)
  dollar_values: { <cat>: float, ... }
}
```

The top-level fields are the player's *primary-pool* valuation; multi-pool
players (e.g. a 1B also rostered in UTIL) have their full per-pool history
in `valuations_by_position` during pipeline execution, but only the
primary pool surfaces into the merged JSON to avoid ambiguity for
downstream consumers.

**2. Position Summary CSV** (`<source>/position_summary.csv`)

The only per-source artifact. Pool-level aggregates that aren't in the
merged JSON — useful for inspecting *how* the engine priced a pool.

```
position: string                          // "C" | "1B" | ... | "UTIL" | "SP" | "RP"
role: string                              // "HITTER" | "SP" | "RP"
rostered_count: integer
replacement_tier_count: integer
total_budget: float
budget_<category>: float                  // category dollar pot (budget_R, budget_HR, ...)
pool_total_z_<category>: float            // sum of settled z's across rostered tier
dollars_per_z_<category>: float           // $/z conversion the engine settled on
replacement_baseline_<category>: float    // RLP archetype's raw value
```

Use this to answer "why did SS get 12% of the hitter budget?" or
"what's the $/z for OBP after the swap-pass?".

---

### Core Data Structures

**Player (shared identity + valuation fields)**

This is the common "person" object. Every valuation entity references this, so you don't duplicate identity metadata across hitter/pitcher types.

```
{
  id: string
  name: string
  team: string
  positions: string[]
  role: "HITTER" | "SP" | "RP"

  // Everything TRP computes lives in the valuation object
  // Multi-position players store valuations per position + top-level (primary)
  valuation: {
    primary_position: string
    normalized_z: { [category]: float }
    total_z: float
    dollar_values: { [category]: float }
    total_dollars: float
    tier: "ROSTERED" | "REPLACEMENT" | "BELOW_REPLACEMENT"

    // Position-specific valuations (for multi-position players)
    valuations_by_position: {
      [position]: PositionValuation
    }
  }
}
```

**PositionValuation (position-specific valuation data)**

Multi-position players are valued at each eligible position during iteration. Each position's valuation is stored separately to enable position-specific exports and dollar calculations.

```
{
  position: string
  normalized_z: { [category]: float }
  total_z: float
  dollar_values: { [category]: float }
  total_dollars: float
  tier: "ROSTERED" | "REPLACEMENT" | "BELOW_REPLACEMENT"
  position_rank: integer  // rank within this position pool (used for deduplication)
}
```

**HitterStats (hitting-only stat payload)**

```
{
  pa: float
  ab: float
  r: float
  hr: float
  rbi: float
  sbn: float          // NOTE: league uses Net SB (SB - CS)
  obp: float
  slg: float
  wrc_plus: float     // optional: used for initial sorting / diagnostics
}
```

**PitcherStats (pitching-only stat payload)**

```
{
  outs: float         // preferred representation; IP = outs / 3
  era: float
  whip: float
  k9: float

  // role-specific counting cats (keep both; one will be 0 by role)
  qs: float           // SP only
  svhd: float         // RP only

  fip: float          // optional: used for initial sorting / diagnostics
}
```

**HitterPlayer (Player + HitterStats)**

```
{
  player: Player
  stats: HitterStats
}
```

**PitcherPlayer (Player + PitcherStats)**

```
{
  player: Player
  stats: PitcherStats
}
```

Design note: we keep a single shared `Player` identity object, but hitters and pitchers are still distinct **valuation entities** because they carry different stat payloads. This avoids “half-empty” objects without duplicating identity fields.

**PositionPool**
```
{
  position: string
  role: "HITTER" | "SP" | "RP"
  roster_slots: integer
  rostered_players: Player[]
  replacement_players: Player[]
  rostered_tier_means: { [category]: float }
  rostered_tier_stdevs: { [category]: float }
  rlp_archetype: { [category]: float }        // Average raw stats of replacement tier
  rlp_raw_z_avg: { [category]: float }        // Average raw Z-scores of replacement tier
  category_budgets: { [category]: float }
  dollars_per_z: { [category]: float }
  total_pool_z: { [category]: float }
  production_share: { [category]: float }
  z_baseline_shift: { [category]: float }     // Shift to ensure rostered players get positive dollars
}
```

**LeagueBudget**
```
{
  total: float
  hitter_budget: float
  pitcher_budget: float
  sp_budget: float
  rp_budget: float
  category_budgets: {
    hitter: { [category]: float }
    sp: { [category]: float }
    rp: { [category]: float }
  }
}
```

---

### Core Functions

**Why No Upfront Position Assignment?**

*IMPORTANT:* TRP does **not** assign primary positions before iteration. Instead, it uses a multi-eligibility approach:

1. **Phase 3a:** Players appear in ALL eligible position pools simultaneously
2. **Phase 3b:** Each pool values players independently, storing position-specific Z-scores
3. **Phase 3c:** Dedupe step assigns each player to their best-ranked position
4. **Phase 3d:** Re-iterate with players in their final position

This is more accurate than upfront assignment because:
- Players are valued at every position they're eligible for
- Natural ranking emerges (best SS might also be best UTIL)
- Handles edge cases (rostered at one position, replacement at another)

---

**BUILD_POSITION_POOLS(players, settings, role, use_eligibility=False)**

This function creates the initial tier structure for each position. The rostered tier is straightforward—top N players by composite metric. The replacement tier uses a percentage band (typically 3%) below the last rostered player, expanding if needed to meet the minimum tier size.

The percentage band approach (from Part 2) ensures the replacement tier adapts to each position's talent distribution rather than assuming a fixed size.

**Key Parameter:** `use_eligibility` (default False)
- When True: Players appear in ALL eligible position pools (multi-eligibility mode for Phase 3a)
- When False: Players only appear in their assigned primary position (single-position mode)

```
BUILD_POSITION_POOLS(players, settings, role, use_eligibility=False):
    pools = []
    categories = GET_CATEGORIES(role, settings)

    FOR each position IN GET_POSITIONS(role, settings):
        pool = NEW PositionPool
        pool.position = position
        pool.role = role
        pool.roster_slots = settings.roster_slots[position] * settings.num_teams

        // Get players for this position
        IF use_eligibility:
            // Multi-eligibility mode: include all eligible players
            position_players = FILTER(players, position IN player.positions)
        ELSE:
            // Single-position mode: only primary position
            position_players = FILTER(players, primary_position == position)

        position_players = SORT_BY(position_players, composite_metric, descending)

        // Initial tier assignment
        pool.rostered_players = position_players[0 : pool.roster_slots]

        // Replacement tier: within X% of last rostered player
        last_rostered_metric = pool.rostered_players[-1].composite_metric
        threshold = last_rostered_metric * (1 - budget_config.replacement_tier_pct)

        replacement_candidates = FILTER(
            position_players[pool.roster_slots :],
            composite_metric >= threshold
        )

        // Enforce minimum tier size
        IF LENGTH(replacement_candidates) < budget_config.min_replacement_tier_size:
            replacement_candidates = position_players[
                pool.roster_slots : pool.roster_slots + budget_config.min_replacement_tier_size
            ]

        pool.replacement_players = replacement_candidates

        pools.APPEND(pool)

    RETURN pools
```

---

**BUILD_UTIL_POOL(hitter_pools, pure_dh_players, settings)**

The UTIL/DH slot is unique—it's not a real position but a flex slot filled by the best available hitter not starting elsewhere. Pure DH players (Shohei Ohtani, Kyle Schwarber) exist, but there aren't enough quality DHs to fill a 12-team UTIL pool on their own.

The solution: after all position pools converge, collect every player in each position's replacement tier. These are players who are below replacement *at their primary position* but still have fantasy value. Geraldo Perdomo, the 13th-best SS, becomes a UTIL candidate. Add pure DH players to this collection, then run the same iteration process to find the best UTIL options.

This must happen *last* because we need converged position pools to identify who falls into replacement tiers.

```
BUILD_UTIL_POOL(hitter_pools, pure_dh_players, settings):
    pool = NEW PositionPool
    pool.position = "UTIL"
    pool.role = "HITTER"
    pool.roster_slots = settings.roster_slots["UTIL"] * settings.num_teams
    
    // Collect all replacement-tier players from every position
    util_candidates = []
    FOR each position_pool IN hitter_pools:
        FOR each player IN position_pool.replacement_players:
            util_candidates.APPEND(player)
        // Also include below-replacement players who might still have UTIL value
        FOR each player IN position_pool.below_replacement:
            util_candidates.APPEND(player)
    
    // Add pure DH players (no other position eligibility)
    FOR each player IN pure_dh_players:
        util_candidates.APPEND(player)
    
    // Remove duplicates (player might be in multiple replacement tiers if multi-position)
    util_candidates = UNIQUE_BY(util_candidates, player.id)
    
    // Sort by composite metric
    util_candidates = SORT_BY(util_candidates, composite_metric, descending)
    
    // Initial tier assignment
    pool.rostered_players = util_candidates[0 : pool.roster_slots]
    
    // Replacement tier using same percentage band logic
    IF LENGTH(pool.rostered_players) > 0:
        last_rostered_metric = pool.rostered_players[-1].composite_metric
        threshold = last_rostered_metric * (1 - budget_config.replacement_tier_pct)
        
        replacement_candidates = FILTER(
            util_candidates[pool.roster_slots :],
            composite_metric >= threshold
        )
        
        IF LENGTH(replacement_candidates) < budget_config.min_replacement_tier_size:
            replacement_candidates = util_candidates[
                pool.roster_slots : pool.roster_slots + budget_config.min_replacement_tier_size
            ]
        
        pool.replacement_players = replacement_candidates
    
    RETURN pool
```

---

**ITERATE_TO_CONVERGENCE(pools, budget_config, track_z_per_pool=False)**

This is the heart of Part 3's methodology. The initial tier assignment uses a composite metric (wRC+), but fantasy leagues score categories separately. A player ranked 8th by wRC+ might rank 13th by total Z if his profile is lopsided.

The iteration loop recalculates Z-scores against each iteration's rostered tier, re-ranks players, and reassigns tiers until membership stabilizes. Typically converges in 2–3 iterations.

**Key Parameter:** `track_z_per_pool` (default False)
- When True: Store Z-scores in `player.valuation.valuations_by_position[pool.position]` (enables multi-position valuations)
- When False: Store Z-scores in top-level `player.valuation` (standard single-position mode)

```
ITERATE_TO_CONVERGENCE(pools, budget_config, track_z_per_pool=False):
    FOR iteration = 1 TO budget_config.max_iterations:
        changes = 0

        FOR each pool IN pools:
            // Step 1: Calculate rostered tier mean and stdev per category
            pool.rostered_tier_means = CALC_MEANS(pool.rostered_players, categories)
            pool.rostered_tier_stdevs = CALC_STDEVS(pool.rostered_players, categories)

            // Step 2: Calculate raw Z-scores for all players
            all_pool_players = pool.rostered_players + pool.replacement_players + pool.below_replacement
            FOR each player IN all_pool_players:
                raw_z = CALC_RAW_Z(player, pool)

                // Step 3: Calculate RLP average raw Z (the baseline shift)
                pool.rlp_raw_z_avg = CALC_MEANS(pool.replacement_players, raw_z)

                // Step 4: Normalize Z-scores (subtract RLP average)
                normalized_z = CALC_NORMALIZED_Z(raw_z, pool.rlp_raw_z_avg)
                total_z = SUM(normalized_z)

                // Step 5: Store Z-scores based on tracking mode
                IF track_z_per_pool:
                    // Create or update position-specific valuation
                    IF pool.position NOT IN player.valuation.valuations_by_position:
                        player.valuation.valuations_by_position[pool.position] = NEW PositionValuation

                    pos_val = player.valuation.valuations_by_position[pool.position]
                    pos_val.position = pool.position
                    pos_val.normalized_z = normalized_z
                    pos_val.total_z = total_z
                    pos_val.position_rank = INDEX_OF(player, all_pool_players)
                ELSE:
                    // Store in top-level valuation
                    player.valuation.normalized_z = normalized_z
                    player.valuation.total_z = total_z

            // Step 6: Re-rank by total Z
            all_pool_players = SORT_BY(all_pool_players, total_z, descending)

            // Step 7: Reassign tiers based on new ranking
            new_rostered = all_pool_players[0 : pool.roster_slots]

            // Check for changes
            old_ids = SET(player.id FOR player IN pool.rostered_players)
            new_ids = SET(player.id FOR player IN new_rostered)
            IF old_ids != new_ids:
                changes += 1

            // Update tiers
            pool.rostered_players = new_rostered
            pool.replacement_players = REBUILD_REPLACEMENT_TIER(all_pool_players, pool)

            // Mark player tiers
            FOR each player IN pool.rostered_players:
                IF track_z_per_pool:
                    player.valuation.valuations_by_position[pool.position].tier = "ROSTERED"
                ELSE:
                    player.valuation.tier = "ROSTERED"

            FOR each player IN pool.replacement_players:
                IF track_z_per_pool:
                    player.valuation.valuations_by_position[pool.position].tier = "REPLACEMENT"
                ELSE:
                    player.valuation.tier = "REPLACEMENT"

        // Check convergence
        IF changes <= budget_config.convergence_threshold:
            BREAK

    RETURN pools
```

---

**DEDUPE_MULTI_POSITION_PLAYERS(pools, replacement_tier_pct, min_replacement_tier_size)**

After multi-eligibility iteration, players who appear in multiple position pools must be assigned to exactly one position. This function implements the deduplication logic.

**Strategy:**
1. For each multi-position player, find their best position (prefer ROSTERED tier, then best position_rank)
2. Assign that as their primary position
3. Remove them from all other pools
4. Slide players up in affected pools to maintain roster sizes

```
DEDUPE_MULTI_POSITION_PLAYERS(pools, replacement_tier_pct, min_replacement_tier_size):
    changes = 0

    // Build map of player_id -> list of (pool, tier, rank)
    player_positions = {}
    FOR each pool IN pools:
        FOR each player IN pool.rostered_players + pool.replacement_players:
            IF player.id NOT IN player_positions:
                player_positions[player.id] = []

            pos_val = player.valuation.valuations_by_position[pool.position]
            player_positions[player.id].APPEND({
                pool: pool,
                tier: pos_val.tier,
                rank: pos_val.position_rank
            })

    // Process each multi-position player
    FOR each player_id, positions IN player_positions:
        IF LENGTH(positions) > 1:
            // Find best position: ROSTERED tier first, then best rank
            best = FIND_BEST_POSITION(positions)  // ROSTERED > REPLACEMENT, then lowest rank

            // Mark as primary position
            player = GET_PLAYER(player_id)
            player.valuation.primary_position = best.pool.position

            // Copy best position valuation to top-level
            best_pos_val = player.valuation.valuations_by_position[best.pool.position]
            player.valuation.tier = best_pos_val.tier

            // Remove from non-primary pools
            FOR each pos IN positions WHERE pos.pool != best.pool:
                REMOVE player FROM pos.pool.rostered_players
                REMOVE player FROM pos.pool.replacement_players
                changes += 1

    // Refill tiers after removing players
    FOR each pool IN pools:
        // Slide players up to maintain roster_slots size
        all_eligible = GET_ALL_ELIGIBLE_PLAYERS_FOR_POSITION(pool.position)
        all_eligible = SORT_BY(all_eligible, composite_metric, descending)

        // Rebuild rostered tier
        pool.rostered_players = all_eligible[0 : pool.roster_slots]

        // Rebuild replacement tier with percentage band
        IF LENGTH(pool.rostered_players) > 0:
            last_rostered_metric = pool.rostered_players[-1].composite_metric
            threshold = last_rostered_metric * (1 - replacement_tier_pct)

            replacement_candidates = FILTER(
                all_eligible[pool.roster_slots :],
                composite_metric >= threshold
            )

            IF LENGTH(replacement_candidates) < min_replacement_tier_size:
                replacement_candidates = all_eligible[
                    pool.roster_slots : pool.roster_slots + min_replacement_tier_size
                ]

            pool.replacement_players = replacement_candidates

    RETURN pools, changes
```

**Helper Function:**
```
FIND_BEST_POSITION(positions):
    // Prefer ROSTERED tier over REPLACEMENT
    rostered_positions = FILTER(positions, tier == "ROSTERED")
    IF LENGTH(rostered_positions) > 0:
        // Multiple ROSTERED: pick best rank (lowest number)
        RETURN MIN_BY(rostered_positions, rank)

    // All REPLACEMENT: pick best rank
    RETURN MIN_BY(positions, rank)
```

---


Implementation detail: for role-specific categories (`QS` for SP, `SVHD` for RP), pitchers of the *other* role should return `0` for that stat so the engine can treat the category set as uniform within a pool.


**CALC_RAW_Z(player, pool)**

The raw Z-score measures how many standard deviations a player is from the rostered tier mean. This puts all categories on the same scale—the "common currency" from Part 3.

Note the inversion for ERA and WHIP: lower is better, so we flip the formula.

```
CALC_RAW_Z(player, pool):
    raw_z = {}
    categories = GET_CATEGORIES(pool.role)
    
    FOR each category IN categories:
        mean = pool.rostered_tier_means[category]
        stdev = pool.rostered_tier_stdevs[category]
        value = player.stats[category]
        
        IF stdev == 0:
            raw_z[category] = 0
        ELSE IF IS_INVERTED(category):  // ERA, WHIP
            raw_z[category] = (mean - value) / stdev
        ELSE:
            raw_z[category] = (value - mean) / stdev
    
    RETURN raw_z
```

---

**CALC_NORMALIZED_Z(raw_z, rlp_raw_z_avg)**

Raw Z-scores are relative to the rostered tier average—not replacement level. To measure value *above replacement*, we subtract the RLP tier's average raw Z in each category.

This is the baseline shift from Part 3: a rostered player who's average in a category gets raw Z = 0, but if the RLP tier averages -1.5 in that category, the rostered player's normalized Z becomes +1.5.

```
CALC_NORMALIZED_Z(raw_z, rlp_raw_z_avg):
    normalized_z = {}

    FOR each category IN raw_z:
        normalized_z[category] = raw_z[category] - rlp_raw_z_avg[category]

    RETURN normalized_z
```

---

**CALC_LEAGUE_BUDGET(settings, budget_config)**

This implements the budget hierarchy. The key splits:
- 70/30 hitter/pitcher (roster construction + predictability)
- 50/50 SP/RP within pitching
- 50/50 rate/counting within hitting (25% each for OBP/SLG, 12.5% each for R/HR/RBI/SB)
- 40% K/9, 15% each other category within pitching pools

```
CALC_LEAGUE_BUDGET(settings, budget_config):
    budget = NEW LeagueBudget
    
    // Total spendable budget (excluding bench reserve)
    budget.total = settings.num_teams * (settings.budget_per_team - settings.bench_reserve)
    
    // Hitter/Pitcher split
    budget.hitter_budget = budget.total * budget_config.hitter_pitcher_split[0]
    budget.pitcher_budget = budget.total * budget_config.hitter_pitcher_split[1]
    
    // SP/RP split
    budget.sp_budget = budget.pitcher_budget * budget_config.sp_rp_split[0]
    budget.rp_budget = budget.pitcher_budget * budget_config.sp_rp_split[1]
    
    // Category budgets
    FOR each category, weight IN budget_config.hitter_category_weights:
        budget.category_budgets.hitter[category] = budget.hitter_budget * weight
    
    FOR each category, weight IN budget_config.sp_category_weights:
        budget.category_budgets.sp[category] = budget.sp_budget * weight
    
    FOR each category, weight IN budget_config.rp_category_weights:
        budget.category_budgets.rp[category] = budget.rp_budget * weight
    
    RETURN budget
```

---

**ALLOCATE_POSITION_BUDGETS(pools, league_budget, budget_config)**

Each position gets a share of category budgets based on actual production contribution—not arbitrary weights. If catchers produce 5.9% of total HR, they get 5.9% of the HR budget.

For rate stats, we weight by plate appearances (500 PA for catchers, 600 PA for others) to reflect that a roster slot filled by a 500 PA catcher contributes less to season-long OBP than a 600 PA first baseman.

```
ALLOCATE_POSITION_BUDGETS(pools, league_budget, budget_config):
    categories = GET_CATEGORIES("HITTER")
    counting_stats = ["R", "HR", "RBI", "SB"]
    rate_stats = ["OBP", "SLG"]
    
    // Calculate total production across all hitter pools
    total_production = {}
    FOR each category IN counting_stats:
        total_production[category] = SUM(
            SUM(player.stats[category] FOR player IN pool.rostered_players)
            FOR pool IN pools
        )
    
    // Calculate total weighted PA for rate stats
    total_weighted_pa = 0
    FOR each pool IN pools:
        pa_weight = budget_config.pa_weights[pool.position] OR budget_config.pa_weights["default"]
        pool_pa = LENGTH(pool.rostered_players) * pa_weight
        pool.weighted_pa = pool_pa
        total_weighted_pa += pool_pa
    
    // Allocate to each position
    FOR each pool IN pools:
        pool.category_budgets = {}
        pool.production_share = {}
        
        // Counting stats: by production share
        FOR each category IN counting_stats:
            pool_production = SUM(player.stats[category] FOR player IN pool.rostered_players)
            pool.production_share[category] = pool_production / total_production[category]
            pool.category_budgets[category] = league_budget.category_budgets.hitter[category] * pool.production_share[category]
        
        // Rate stats: by PA share
        FOR each category IN rate_stats:
            pool.production_share[category] = pool.weighted_pa / total_weighted_pa
            pool.category_budgets[category] = league_budget.category_budgets.hitter[category] * pool.production_share[category]
    
    RETURN pools
```

---

**CALC_DOLLARS_PER_Z(pools)**

Once we have category budgets per position, we divide by total Z-scores to get the conversion rate. This is the $/Z rate that translates statistical value into economic value.

Note: we sum only positive Z-scores. Negative Z subtracts from player value but doesn't "add" to the pool of dollars being distributed.

```
CALC_DOLLARS_PER_Z(pools):
    FOR each pool IN pools:
        pool.dollars_per_z = {}
        pool.total_pool_z = {}
        
        FOR each category IN pool.category_budgets:
            // Sum of positive Z-scores in rostered tier
            pool.total_pool_z[category] = SUM(
                MAX(0, player.computed.normalized_z[category])
                FOR player IN pool.rostered_players
            )
            
            IF pool.total_pool_z[category] > 0:
                pool.dollars_per_z[category] = pool.category_budgets[category] / pool.total_pool_z[category]
            ELSE:
                pool.dollars_per_z[category] = 0
    
    RETURN pools
```

---

**DISTRIBUTE_PLAYER_DOLLARS(player, pool, store_in_position_valuation=False)**

The final step: multiply each player's normalized Z by the $/Z rate for their position-category. Sum across categories for total dollar value.

Negative Z-scores produce negative dollar contributions—a player who hurts you in a category is penalized accordingly.

**Key Parameter:** `store_in_position_valuation` (default False)
- When True: Read Z-scores from `valuations_by_position[pool.position]` and store dollars back there (enables position-specific dollar values)
- When False: Use top-level Z-scores and return dollars without storing

**Baseline Shift:** For rostered players, applies `z_baseline_shift` to ensure no negative total dollars.

```
DISTRIBUTE_PLAYER_DOLLARS(player, pool, store_in_position_valuation=False):
    dollar_values = {}

    // Determine which Z-scores to use
    IF store_in_position_valuation AND pool.position IN player.valuation.valuations_by_position:
        normalized_z = player.valuation.valuations_by_position[pool.position].normalized_z
    ELSE:
        normalized_z = player.valuation.normalized_z

    // Calculate dollars per category
    FOR each category IN normalized_z:
        z_value = normalized_z[category]
        rate = pool.dollars_per_z[category]

        // Apply baseline shift for rostered players (handles negative Z)
        IF player IN pool.rostered_players:
            baseline_shift = pool.z_baseline_shift[category]
            adjusted_z = MAX(0, z_value + baseline_shift)
        ELSE:
            adjusted_z = z_value

        dollar_values[category] = adjusted_z * rate

    total_dollars = SUM(dollar_values)

    // Store in position valuation if requested
    IF store_in_position_valuation AND pool.position IN player.valuation.valuations_by_position:
        player.valuation.valuations_by_position[pool.position].dollar_values = dollar_values
        player.valuation.valuations_by_position[pool.position].total_dollars = total_dollars

    RETURN dollar_values
```

---

**CALC_BASELINE_SHIFT(pool)**

Calculates the baseline shift needed to ensure all rostered players have positive total dollars. For each category, finds the minimum Z-score in the rostered tier. If negative, shifts all Z-scores up by that amount during dollar distribution.

This preserves relative differences while preventing negative total valuations for rosterable players.

```
CALC_BASELINE_SHIFT(pool):
    pool.z_baseline_shift = {}

    FOR each category IN pool.rostered_tier_means:
        // Find minimum Z-score in rostered tier
        min_z = MIN(player.valuation.normalized_z[category] FOR player IN pool.rostered_players)

        // If any rostered player has negative Z, shift everything up
        IF min_z < 0:
            pool.z_baseline_shift[category] = -min_z
        ELSE:
            pool.z_baseline_shift[category] = 0

    RETURN pool
```

---

### Helper Functions

**IS_INVERTED(category)**
```
RETURN category IN ["ERA", "WHIP"]
```

**GET_CATEGORIES(role, settings)**
```
IF role == "HITTER":
    RETURN settings.hitter_categories
ELSE IF role == "SP":
    RETURN settings.sp_categories
ELSE IF role == "RP":
    RETURN settings.rp_categories
```

**CALC_MEANS(players, field)**
```
values = [player.stats[field] OR player.valuation[field] FOR player IN players]
RETURN SUM(values) / LENGTH(values)
```

**CALC_STDEVS(players, field)**
```
values = [player.stats[field] OR player.valuation[field] FOR player IN players]
mean = CALC_MEANS(players, field)
variance = SUM((v - mean)^2 FOR v IN values) / LENGTH(values)
RETURN SQRT(variance)
```

**GET_POSITION_VALUATION(player, position)**

Helper function for any caller that needs a multi-pool player's
valuation *as seen by a specific pool*. Reads from
`valuations_by_position[position]` when present, falls back to
top-level. Still used internally by Phase 5's swap-pass and by the
budget validators; the per-position detailed CSVs that used to call
this have been removed (their data was already in the merged JSON).

```
GET_POSITION_VALUATION(player, position):
    """
    Returns: (total_z, normalized_z, dollar_values, total_dollars, tier)
    """
    IF position IN player.valuation.valuations_by_position:
        pos_val = player.valuation.valuations_by_position[position]
        RETURN (
            pos_val.total_z,
            pos_val.normalized_z,
            pos_val.dollar_values,
            pos_val.total_dollars,
            pos_val.tier
        )
    ELSE:
        // Fallback for single-position players
        RETURN (
            player.valuation.total_z,
            player.valuation.normalized_z,
            player.valuation.dollar_values,
            player.valuation.total_dollars,
            player.valuation.tier
        )
```

---

### Validation Checks

Before outputting, validate:

1. **Budget Balance:** Sum of all rostered player dollars ≈ total league budget (±$1)
2. **No Orphan Players:** Every player with projections is assigned to exactly one position pool
3. **Tier Consistency:** Rostered tier size equals roster slots × num teams for each position
4. **Z-Score Sanity:** RLP players should have total normalized Z near 0
5. **Dollar Sanity:** No rostered player should have negative total dollars (baseline shift prevents this)
6. **Position Valuation Hydration:** All rostered/replacement players in hitter pools must have position-specific dollar values in `valuations_by_position[position]`

---

### Control Flow

Putting it all together—the complete 10-phase pipeline from raw projections to dollar values. Key innovations: multi-eligibility iteration (Phase 3), position-specific dollar hydration (Phase 5), and baseline shift for negative Z-scores.

```
MAIN():
    // ========================================================================
    // Phase 1: Initialize
    // ========================================================================
    hitter_players = LOAD_BATTERS(batters_file)
    pitcher_players = LOAD_PITCHERS(pitchers_file)
    league_settings = LOAD_LEAGUE_SETTINGS(league_file)
    budget_config = LOAD_BUDGET_CONFIG(budget_config_file)
    league_budget = CALC_LEAGUE_BUDGET(league_settings, budget_config)

    // ========================================================================
    // Phase 2: Split by role
    // ========================================================================
    hitters = [hp.player FOR hp IN hitter_players]

    // Identify pure DH players (only DH/UTIL eligibility)
    pure_dh_players = FILTER(hitters, positions SUBSET_OF {"DH", "UTIL"})
    regular_hitters = FILTER(hitters, NOT IN pure_dh_players)

    starters = [pp.player FOR pp IN pitcher_players IF pp.player.role == "SP"]
    relievers = [pp.player FOR pp IN pitcher_players IF pp.player.role == "RP"]

    // ========================================================================
    // Phase 3: Build hitter pools (multi-eligible with deduplication)
    // ========================================================================

    // Phase 3a: Build pools with multi-eligibility
    // Players appear in ALL eligible position pools simultaneously
    hitter_pools = BUILD_POSITION_POOLS(
        regular_hitters,
        roster_slots,
        num_teams,
        "HITTER",
        use_eligibility=True  // Multi-eligibility mode
    )

    // Phase 3b: Iterate with per-pool Z-score tracking
    // Stores position-specific valuations in valuations_by_position
    hitter_pools = ITERATE_TO_CONVERGENCE(
        hitter_pools,
        budget_config,
        track_z_per_pool=True  // Store Z-scores per position
    )

    // Phase 3c: Deduplicate multi-position players
    // Assigns each player to their best-ranked position
    hitter_pools, dedupe_changes = DEDUPE_MULTI_POSITION_PLAYERS(
        hitter_pools,
        budget_config.replacement_tier_pct,
        budget_config.min_replacement_tier_size
    )

    // Phase 3d: Re-iterate after dedupe (if changes occurred)
    // Now single-position mode since players are assigned
    IF dedupe_changes > 0:
        hitter_pools = ITERATE_TO_CONVERGENCE(
            hitter_pools,
            budget_config,
            track_z_per_pool=False  // Standard single-position mode
        )

    // ========================================================================
    // Phase 4: Build UTIL pool from stabilized pools
    // ========================================================================

    // Phase 4a: Build UTIL pool from replacement-tier players + pure DHs
    // Must happen AFTER position pools converge
    util_pool = BUILD_UTIL_POOL(
        hitter_pools,  // Use converged pools
        pure_dh_players,
        roster_slots,
        num_teams,
        budget_config.replacement_tier_pct,
        budget_config.min_replacement_tier_size
    )

    // Phase 4b: Iterate UTIL pool with composite RLP baseline
    // Use track_z_per_pool to preserve original position valuations
    util_pool = ITERATE_TO_CONVERGENCE(
        {"UTIL": util_pool},
        budget_config,
        track_z_per_pool=True  // Preserve position-specific valuations
    )["UTIL"]

    // Phase 4c: Assign primary positions and tiers for UTIL players
    ASSIGN_PRIMARY_POSITION_FROM_POOL(util_pool)

    // Copy UTIL valuations to top-level for budget allocation
    FOR each player IN util_pool.rostered_players + util_pool.replacement_players:
        IF "UTIL" IN player.valuation.valuations_by_position:
            util_val = player.valuation.valuations_by_position["UTIL"]
            player.valuation.normalized_z = util_val.normalized_z
            player.valuation.total_z = util_val.total_z

    // Update tier attributes to match UTIL pool
    ASSIGN_PLAYER_TIERS(util_pool, track_z_per_pool=False)

    // Add UTIL to hitter pools
    hitter_pools["UTIL"] = util_pool

    // ========================================================================
    // Phase 5: Allocate hitter budgets + distribute dollars
    // ========================================================================

    // Allocate category budgets based on production share
    hitter_pools = ALLOCATE_POSITION_BUDGETS(hitter_pools, league_budget, budget_config)

    // Calculate $/Z conversion rates
    hitter_pools = CALC_POOL_DOLLARS_PER_Z(hitter_pools)

    // Calculate baseline shift to handle negative Z-scores
    hitter_pools = CALC_BASELINE_SHIFT(hitter_pools)

    // Distribute dollars to all hitter players
    // store_in_position_valuation=True enables position-specific dollar values
    FOR each pos, pool IN hitter_pools:
        FOR each player IN pool.rostered_players + pool.replacement_players:
            dollar_values = DISTRIBUTE_PLAYER_DOLLARS(
                player,
                pool,
                store_in_position_valuation=True  // Store in valuations_by_position[pos]
            )

            // Store at top-level if this is player's primary position
            IF player.valuation.primary_position == pos:
                player.valuation.dollar_values = dollar_values
                player.valuation.total_dollars = SUM(dollar_values)

    // Validate position valuations are hydrated
    VALIDATE_POSITION_VALUATION_HYDRATION(hitter_pools)

    // ========================================================================
    // Phase 6: Build pitcher pools
    // ========================================================================

    // Phase 6a: Build SP pool
    sp_pool = BUILD_PITCHER_POOL(
        starters,
        roster_slots,
        num_teams,
        "SP",
        budget_config.replacement_tier_pct,
        budget_config.min_replacement_tier_size
    )

    // Phase 6b: Iterate SP pool
    sp_pool = ITERATE_TO_CONVERGENCE({"SP": sp_pool}, budget_config)["SP"]

    // Phase 6c: Build RP pool
    rp_pool = BUILD_PITCHER_POOL(
        relievers,
        roster_slots,
        num_teams,
        "RP",
        budget_config.replacement_tier_pct,
        budget_config.min_replacement_tier_size
    )

    // Phase 6d: Iterate RP pool
    rp_pool = ITERATE_TO_CONVERGENCE({"RP": rp_pool}, budget_config)["RP"]

    // ========================================================================
    // Phase 7: Allocate pitcher budgets
    // ========================================================================

    sp_pool = ALLOCATE_POOL_BUDGET(sp_pool, league_budget.sp_budget, budget_config.sp_category_weights)
    rp_pool = ALLOCATE_POOL_BUDGET(rp_pool, league_budget.rp_budget, budget_config.rp_category_weights)

    sp_pool = CALC_POOL_DOLLARS_PER_Z(sp_pool)
    rp_pool = CALC_POOL_DOLLARS_PER_Z(rp_pool)

    sp_pool = CALC_BASELINE_SHIFT(sp_pool)
    rp_pool = CALC_BASELINE_SHIFT(rp_pool)

    // ========================================================================
    // Phase 8: Distribute pitcher dollars
    // ========================================================================

    FOR each pool IN [sp_pool, rp_pool]:
        // Assign primary positions
        ASSIGN_PRIMARY_POSITION_FROM_POOL(pool)

        // Distribute dollars (pitchers don't need position-specific storage)
        FOR each player IN pool.rostered_players + pool.replacement_players:
            dollar_values = DISTRIBUTE_PLAYER_DOLLARS(player, pool)
            player.valuation.dollar_values = dollar_values
            player.valuation.total_dollars = SUM(dollar_values)

    // ========================================================================
    // Phase 9: Validate
    // ========================================================================

    all_pools = hitter_pools + sp_pool + rp_pool

    VALIDATE_BUDGET_BALANCE(all_pools, league_budget)
    VALIDATE_TIER_COUNTS(all_pools, roster_slots, num_teams)
    VALIDATE_RLP_Z_SCORES(all_pools)
    VALIDATE_POSITION_VALUATION_HYDRATION(hitter_pools)

    // ========================================================================
    // Phase 10: Output
    // ========================================================================
    // Per-source pipeline only writes pool-level aggregates here. The
    // merged per-player JSON is written once across all sources by the
    // outer ``run_all_valuations`` driver — see below.

    WRITE_POSITION_SUMMARY_CSV(output_dir / "position_summary.csv", all_pools)

    // Per-player valuations returned in-memory so the multi-source driver
    // can merge them by source label.
    hitter_valuations  = BUILD_PLAYER_VALUATIONS(hitter_pools)
    pitcher_valuations = BUILD_PLAYER_VALUATIONS(sp_pool + rp_pool)
    return (hitter_valuations, pitcher_valuations, hitter_rostered_rlp_ids, pitcher_rostered_rlp_ids)


// Outer driver (run_all_valuations) — once across all 5 sources
FOR source IN ("projections", "projs_updated", "ros", "synthetic", "current"):
    (h_vals, p_vals, h_ids, p_ids) = RUN_TRP_VALUATION(source, output_dir/source_label)
    hitter_vals_by_source[source_label]  = h_vals
    pitcher_vals_by_source[source_label] = p_vals
    IF source == "current":
        current_hitter_ids  = h_ids
        current_pitcher_ids = p_ids

// Inject savant pct_rnks using the current-source rostered+RLP universe
INJECT_SAVANT_PCT_RNKS(batters_data, pitchers_data, current_hitter_ids, current_pitcher_ids)

// Write the canonical merged JSONs (only) at the output root
WRITE_MERGED_PLAYER_JSON(output_dir / "hitters.json",  batters_data,  hitter_vals_by_source)
WRITE_MERGED_PLAYER_JSON(output_dir / "pitchers.json", pitchers_data, pitcher_vals_by_source)
```

---

### Why This Architecture? Design Decisions Explained

This section explains the key architectural decisions that differentiate the implementation from simpler approaches.

**1. Why multi-eligibility + dedupe instead of upfront position assignment?**

*Problem:* A player eligible at 2B, SS, and UTIL could be the best at any of those positions. Assigning them upfront to their "primary" position misses nuance.

*Solution:* Multi-eligibility iteration (Phase 3a-3c):
- Phase 3a: Players appear in ALL eligible position pools
- Phase 3b: Each pool values them independently
- Phase 3c: After convergence, assign to their best-ranked position

*Benefits:*
- More accurate: values players at every position they're eligible for
- Natural ranking: best SS might also be best UTIL—let iteration decide
- Handles edge cases: player rostered at one position, replacement at another

*Example:* Trevor Story might be:
- 11th-best SS (replacement tier at SS)
- 9th-best UTIL (rostered tier at UTIL)

Dedupe assigns him to UTIL, but preserves his SS valuation for exports.

---

**2. Why track_z_per_pool flag in ITERATE_TO_CONVERGENCE?**

*Problem:* During multi-eligibility iteration, we need to store Z-scores for EACH position a player is eligible at—not just overwrite with the last pool processed.

*Solution:* `track_z_per_pool=True` stores valuations in `player.valuation.valuations_by_position[position]` instead of overwriting top-level valuation.

*Benefits:*
- Enables simultaneous valuation at multiple positions
- Preserves position-specific Z-scores for dedupe logic
- Allows UTIL iteration without clobbering original position tiers

*When to use:*
- True: Multi-eligibility mode (Phase 3b, Phase 4b)
- False: Single-position mode (Phase 3d, Phase 6)

---

**3. Why position-specific valuation hydration (store_in_position_valuation)?**

*Problem:* Multi-position players have different Z-scores at different positions. Should they have different dollar values too?

*Answer:* Yes! A player's dollar value depends on their position pool's $/Z rates.

*Solution:* `DISTRIBUTE_PLAYER_DOLLARS(store_in_position_valuation=True)`:
- Reads Z-scores from `valuations_by_position[position]`
- Applies position-specific $/Z rates
- Stores dollars back into `valuations_by_position[position]`

*Benefits:*
- Multi-position players carry accurate dollar values per pool internally
- Phase 5's swap-pass + budget validators read the per-pool view to keep
  cross-pool $/z math consistent
- Example: Trevor Story = $17 at UTIL (rostered), -$0.10 at SS (replacement)

*Consumer view:* the merged `hitters.json` / `pitchers.json` surfaces
each player's *primary-pool* valuation under `valuations[source]` (one
canonical position per source). The per-pool history stays internal to
the pipeline.

---

**4. Why baseline shift for negative Z-scores (CALC_BASELINE_SHIFT)?**

*Problem:* Some rostered players have negative Z-scores in weak categories. Without adjustment, they'd have negative dollar values in those categories, potentially resulting in negative total dollars.

*Example:* A catcher might be -0.5 Z in SBN. At $2/Z, that's -$1 in SB value. If this catcher is barely positive in other categories, their total dollar value might be negative—despite being rosterable.

*Solution:* `CALC_BASELINE_SHIFT` finds the minimum Z-score in each category's rostered tier. If negative, shifts all Z-scores up by that amount before dollar calculation (only for rostered players).

*Benefits:*
- Ensures all rostered players have positive total dollars
- Maintains relative differences (gaps between players preserved)
- Prevents budget allocation errors

*Example:*
- C pool: worst rostered catcher has -0.8 Z in SBN
- Baseline shift: +0.8 applied to all rostered catchers' SBN Z-scores
- Result: worst catcher gets 0 * $2 = $0 in SBN (not -$1.60)

---

**5. Why `rlp_archetype` stores raw stats (not Z-scores)?**

*Clarification:* The `rlp_archetype` field in PositionPool stores the average **raw statistics** (R, HR, RBI, etc.) of the replacement tier—not Z-scores.

*Purpose:* Diagnostic / export. Shows the "typical replacement-level player" stat line for each position.

*Contrast with `rlp_raw_z_avg`:* This stores the average **raw Z-scores** of the replacement tier, used for normalization (baseline shift in Part 3).

---

**6. Why dedupe after multi-eligibility convergence?**

*Problem:* Players in multiple pools inflate roster counts. You can't distribute dollars until each player is in exactly one pool.

*Solution:* Dedupe (Phase 3c) runs AFTER multi-eligibility iteration converges:
- Identifies players in multiple pools
- Assigns each to their best-ranked position
- Removes from non-primary pools
- Slides players up to maintain roster sizes

*Why not dedupe first?* We need converged Z-scores to know which position is "best" for each player. Deduping before iteration would use composite metrics (wRC+), which don't reflect category-based fantasy value.

---

**7. Why UTIL pool uses composite RLP baseline?**

*Problem:* UTIL pool draws from multiple positions' replacement tiers. Each position has different baseline stats. What's the UTIL replacement level?

*Solution:* UTIL iteration (Phase 4b) calculates `rlp_raw_z_avg` from the UTIL replacement tier itself—a composite of players from all positions.

*Why track_z_per_pool=True for UTIL?* Preserves original position valuations (e.g., Trevor Story's SS valuation) while creating new UTIL valuations. This enables exports to show both:
- SS export: Trevor Story's SS-specific tier/Z/dollars
- UTIL export: Trevor Story's UTIL-specific tier/Z/dollars

---

### What We've Built

This blueprint specifies:

1. **Input/Output contracts** — exactly what data goes in and comes out
2. **Data structures** — Player, PositionValuation, PositionPool, and LeagueBudget objects
3. **Core algorithms** — multi-eligibility iteration, deduplication, Z-score calculation, budget allocation, position-specific dollar hydration
4. **Multi-position player handling** — valuation at all eligible positions with intelligent deduplication
5. **UTIL pool construction** — collecting replacement-tier players to fill the flex slot
6. **Baseline shift mechanism** — ensuring rostered players have positive dollar values
7. **Validation checks** — sanity tests including position valuation hydration
8. **Control flow** — the 10-phase pipeline from raw projections to position-specific dollar values
9. **Design rationale** — explanations of key architectural decisions

Hand this document to any competent developer or LLM, and they can build a working TRP implementation in their language of choice, complete with multi-position player handling and position-specific valuations.

---

*TRP is a valuation framework developed within the MTBL (Metaball) ecosystem. It consumes projections from any source and outputs market-calibrated player values for fantasy baseball.*
