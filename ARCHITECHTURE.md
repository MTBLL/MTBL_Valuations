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

The system produces multiple output files:

**1. Player Valuations File** (`valuations.csv`) -- for quick debug and draft day quick looks

```
player_id: string
name: string
position: string (primary position used for valuation)
role: string ("HITTER" | "SP" | "RP")
total_z: float
dollar_value: float
z_R: float (hitters only)
z_HR: float (hitters only)
z_RBI: float (hitters only)
z_SB: float (hitters only)
z_OBP: float (hitters only)
z_SLG: float (hitters only)
z_IP: float (pitchers only)
z_ERA: float (pitchers only)
z_WHIP: float (pitchers only)
z_K9: float (pitchers only)
z_QS: float (SP only)
z_SVHD: float (RP only)
dollar_R: float (hitters only)
dollar_HR: float (hitters only)
... (dollar value per category)
tier: string ("ROSTERED" | "REPLACEMENT" | "BELOW_REPLACEMENT")
```

**2. Position Summary File** (`position_summary.csv`) -- for quick debug and draft day quick looks

```
position: string
role: string
rostered_count: integer
replacement_tier_count: integer
total_budget: float
dollars_per_z_R: float
dollars_per_z_HR: float
... ($/Z for each category)
replacement_baseline_R: float
replacement_baseline_HR: float
... (RLP archetype stats)
```

**3. Hitters JSON** (`hitters.json`)
This should match the upstream batters schema and append a new stats.valuations object that captures all the z-scores and shekels that are league specific.  This JSON will feed the loader pipe with easy translation to postgres db.

**4. Pitchers JSON** (`pitchers.json`)
This should match the upstream pitchers schema and append a new stats.valuations object that captures all the z-scores and shekels that are league specific.  This JSON will feed the loader pipe with easy translation to postgres db.

---

### Core Data Structures

**Player (shared identity + computed fields)**

This is the common “person” object. Every valuation entity references this, so you don’t duplicate identity metadata across hitter/pitcher types.

```
{
  id: string
  name: string
  team: string
  positions: string[]
  role: "HITTER" | "SP" | "RP"

  // everything TRP computes lives here so it stays identical across hitter/pitcher flows
  computed: {
    primary_position: string
    raw_z: { [category]: float }
    normalized_z: { [category]: float }
    total_z: float
    dollar_values: { [category]: float }
    total_dollars: float
    tier: "ROSTERED" | "REPLACEMENT" | "BELOW_REPLACEMENT"
  }
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
  rlp_archetype: { [category]: float }
  rlp_raw_z_avg: { [category]: float }
  category_budgets: { [category]: float }
  dollars_per_z: { [category]: float }
  total_pool_z: { [category]: float }
  production_share: { [category]: float }
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

**ASSIGN_PRIMARY_POSITIONS(players, settings)**

Multi-position players create a challenge: where do you value them? A player eligible at 2B and SS could anchor either position. TRP assigns each player to their most valuable position—the scarcest one where they'd be rostered.

This function processes positions from scarcest to deepest, assigning players to maximize positional value.

```
ASSIGN_PRIMARY_POSITIONS(players, settings):
    // Sort positions by scarcity (fewest roster slots first)
    position_order = SORT_BY(settings.roster_slots, ascending)
    
    assigned = {}
    
    FOR each position IN position_order:
        eligible = FILTER(players, position IN player.positions AND player.id NOT IN assigned)
        slots = settings.roster_slots[position] * settings.num_teams
        
        // Sort by composite metric (wRC+ for hitters, FIP for pitchers)
        eligible = SORT_BY(eligible, composite_metric, descending)
        
        // Assign top N players to this position
        FOR i = 0 TO slots + (slots * 0.5):  // Include replacement tier buffer
            IF i < LENGTH(eligible):
                assigned[eligible[i].id] = position
                eligible[i].computed.primary_position = position
    
    RETURN players
```

---

**BUILD_POSITION_POOLS(players, settings, role)**

This function creates the initial tier structure for each position. The rostered tier is straightforward—top N players by composite metric. The replacement tier uses a percentage band (typically 3%) below the last rostered player, expanding if needed to meet the minimum tier size.

The percentage band approach (from Part 2) ensures the replacement tier adapts to each position's talent distribution rather than assuming a fixed size.

```
BUILD_POSITION_POOLS(players, settings, role):
    pools = []
    categories = GET_CATEGORIES(role, settings)
    
    FOR each position IN GET_POSITIONS(role, settings):
        pool = NEW PositionPool
        pool.position = position
        pool.role = role
        pool.roster_slots = settings.roster_slots[position] * settings.num_teams
        
        // Get players assigned to this position
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

**ITERATE_TO_CONVERGENCE(pools, budget_config)**

This is the heart of Part 3's methodology. The initial tier assignment uses a composite metric (wRC+), but fantasy leagues score categories separately. A player ranked 8th by wRC+ might rank 13th by total Z if his profile is lopsided.

The iteration loop recalculates Z-scores against each iteration's rostered tier, re-ranks players, and reassigns tiers until membership stabilizes. Typically converges in 2–3 iterations.

```
ITERATE_TO_CONVERGENCE(pools, budget_config):
    FOR iteration = 1 TO budget_config.max_iterations:
        changes = 0
        
        FOR each pool IN pools:
            // Step 1: Calculate rostered tier mean and stdev per category
            pool.rostered_tier_means = CALC_MEANS(pool.rostered_players, categories)
            pool.rostered_tier_stdevs = CALC_STDEVS(pool.rostered_players, categories)
            
            // Step 2: Calculate raw Z-scores for all players
            all_pool_players = pool.rostered_players + pool.replacement_players + pool.below_replacement
            FOR each player IN all_pool_players:
                player.computed.raw_z = CALC_RAW_Z(player, pool)
            
            // Step 3: Calculate RLP average raw Z (the baseline shift)
            pool.rlp_raw_z_avg = CALC_MEANS(pool.replacement_players, raw_z)
            
            // Step 4: Normalize Z-scores (subtract RLP average)
            FOR each player IN all_pool_players:
                player.computed.normalized_z = CALC_NORMALIZED_Z(player, pool)
                player.computed.total_z = SUM(player.computed.normalized_z)
            
            // Step 5: Re-rank by total Z
            all_pool_players = SORT_BY(all_pool_players, total_z, descending)
            
            // Step 6: Reassign tiers based on new ranking
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
                player.computed.tier = "ROSTERED"
            FOR each player IN pool.replacement_players:
                player.computed.tier = "REPLACEMENT"
        
        // Check convergence
        IF changes <= budget_config.convergence_threshold:
            BREAK
    
    RETURN pools
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

**CALC_NORMALIZED_Z(player, pool)**

Raw Z-scores are relative to the rostered tier average—not replacement level. To measure value *above replacement*, we subtract the RLP tier's average raw Z in each category.

This is the baseline shift from Part 3: a rostered player who's average in a category gets raw Z = 0, but if the RLP tier averages -1.5 in that category, the rostered player's normalized Z becomes +1.5.

```
CALC_NORMALIZED_Z(player, pool):
    normalized_z = {}
    
    FOR each category IN player.computed.raw_z:
        normalized_z[category] = player.computed.raw_z[category] - pool.rlp_raw_z_avg[category]
    
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

**CALC_PLAYER_DOLLARS(player, pool)**

The final step: multiply each player's normalized Z by the $/Z rate for their position-category. Sum across categories for total dollar value.

Negative Z-scores produce negative dollar contributions—a player who hurts you in a category is penalized accordingly.

```
CALC_PLAYER_DOLLARS(player, pool):
    dollar_values = {}
    
    FOR each category IN player.computed.normalized_z:
        z = player.computed.normalized_z[category]
        rate = pool.dollars_per_z[category]
        dollar_values[category] = z * rate
    
    RETURN dollar_values
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
values = [player.stats[field] OR player.computed[field] FOR player IN players]
RETURN SUM(values) / LENGTH(values)
```

**CALC_STDEVS(players, field)**
```
values = [player.stats[field] OR player.computed[field] FOR player IN players]
mean = CALC_MEANS(players, field)
variance = SUM((v - mean)^2 FOR v IN values) / LENGTH(values)
RETURN SQRT(variance)
```

---

### Validation Checks

Before outputting, validate:

1. **Budget Balance:** Sum of all rostered player dollars ≈ total league budget (±$1)
2. **No Orphan Players:** Every player with projections is assigned to exactly one position pool
3. **Tier Consistency:** Rostered tier size equals roster slots × num teams for each position
4. **Z-Score Sanity:** RLP players should have total normalized Z near 0
5. **Dollar Sanity:** No rostered player should have negative total dollars (below replacement should be rare)

---

### Control Flow

Putting it all together—the complete pipeline from raw projections to dollar values:

```
MAIN():
    // Phase 1: Initialize
    projections = LOAD_PROJECTIONS("projections.csv")
    settings = LOAD_SETTINGS("league_settings.json")
    budget_config = LOAD_BUDGET_CONFIG("budget_config.json")
    
    // Phase 2: Assign primary positions (scarcity-first allocation)
    players = ASSIGN_PRIMARY_POSITIONS(projections, settings)
    
    // Phase 3: Split by role
    hitters = FILTER(players, role == "HITTER")
    pure_dh_players = FILTER(hitters, positions == ["DH"])
    starters = FILTER(players, role == "SP")
    relievers = FILTER(players, role == "RP")
    
    // Phase 4: Build position pools and iterate to convergence
    hitter_pools = BUILD_POSITION_POOLS(hitters, settings, "HITTER")
    hitter_pools = ITERATE_TO_CONVERGENCE(hitter_pools, budget_config)
    
    // Phase 5: Build UTIL pool from replacement-tier players + pure DHs
    // This must happen AFTER position pools converge so we know who's below replacement
    util_pool = BUILD_UTIL_POOL(hitter_pools, pure_dh_players, settings)
    util_pool = ITERATE_TO_CONVERGENCE([util_pool], budget_config)[0]
    hitter_pools.APPEND(util_pool)
    
    // Phase 6: Build pitcher pools
    sp_pool = BUILD_SINGLE_POOL(starters, settings, "SP")
    sp_pool = ITERATE_TO_CONVERGENCE([sp_pool], budget_config)[0]
    
    rp_pool = BUILD_SINGLE_POOL(relievers, settings, "RP")
    rp_pool = ITERATE_TO_CONVERGENCE([rp_pool], budget_config)[0]
    
    // Phase 7: Calculate league budget structure
    league_budget = CALC_LEAGUE_BUDGET(settings, budget_config)
    
    // Phase 8: Allocate category budgets to positions
    hitter_pools = ALLOCATE_POSITION_BUDGETS(hitter_pools, league_budget, budget_config)
    sp_pool = ALLOCATE_POOL_BUDGET(sp_pool, league_budget.sp_budget, budget_config.sp_category_weights)
    rp_pool = ALLOCATE_POOL_BUDGET(rp_pool, league_budget.rp_budget, budget_config.rp_category_weights)
    
    // Phase 9: Convert Z-scores to dollars
    hitter_pools = CALC_DOLLARS_PER_Z(hitter_pools)
    sp_pool = CALC_DOLLARS_PER_Z([sp_pool])[0]
    rp_pool = CALC_DOLLARS_PER_Z([rp_pool])[0]
    
    // Phase 10: Value each player
    FOR each pool IN [hitter_pools..., sp_pool, rp_pool]:
        FOR each player IN pool.rostered_players + pool.replacement_players:
            player.computed.dollar_values = CALC_PLAYER_DOLLARS(player, pool)
            player.computed.total_dollars = SUM(player.computed.dollar_values)
    
    // Phase 11: Validate and normalize
    total_allocated = SUM(all player.computed.total_dollars WHERE tier == "ROSTERED")
    IF total_allocated != league_budget.total:
        NORMALIZE_TO_BUDGET(all_players, league_budget.total)
    
    // Phase 12: Output
    WRITE_VALUATIONS("valuations.csv", all_players)
    WRITE_POSITION_SUMMARY("position_summary.csv", all_pools)
```

---

### What We've Built

This blueprint specifies:

1. **Input/Output contracts** — exactly what data goes in and comes out
2. **Data structures** — Player, PositionPool, and LeagueBudget objects
3. **Core algorithms** — iteration, Z-score calculation, budget allocation, dollar conversion
4. **UTIL pool construction** — collecting replacement-tier players to fill the flex slot
5. **Validation checks** — sanity tests before output
6. **Control flow** — the 12-phase pipeline from raw projections to dollar values

Hand this document to any competent developer or LLM, and they can build a working TRP implementation in their language of choice.

---

*TRP is a valuation framework developed within the MTBL (Metaball) ecosystem. It consumes projections from any source and outputs market-calibrated player values for fantasy baseball.*
