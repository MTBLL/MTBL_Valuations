"""Budget calculation and allocation functions."""

from __future__ import annotations

import statistics
from typing import Any

from ..domain.models import LeagueBudget, PositionPool
from .valuation import get_player_stat


def calc_league_budget(
    league_settings: dict[str, Any], budget_config: dict[str, Any]
) -> LeagueBudget:
    """Calculate league-wide budget structure."""
    num_teams = league_settings["num_teams"]
    budget_per_team = league_settings["auction_budget"]
    bench_reserve = budget_config["bench_reserve_per_team"]

    # Total spendable budget (excluding bench reserve)
    total = num_teams * (budget_per_team - bench_reserve)

    # Hitter/Pitcher split
    hitter_pct, pitcher_pct = budget_config["hitter_pitcher_split"]
    hitter_budget = total * hitter_pct
    pitcher_budget = total * pitcher_pct

    # SP/RP split
    sp_pct, rp_pct = budget_config["sp_rp_split"]
    sp_budget = pitcher_budget * sp_pct
    rp_budget = pitcher_budget * rp_pct

    # Category budgets
    category_budgets: dict[str, dict[str, float]] = {
        "hitter": {},
        "sp": {},
        "rp": {},
    }

    for category, weight in budget_config["hitter_category_weights"].items():
        category_budgets["hitter"][category] = hitter_budget * weight

    for category, weight in budget_config["sp_category_weights"].items():
        category_budgets["sp"][category] = sp_budget * weight

    for category, weight in budget_config["rp_category_weights"].items():
        category_budgets["rp"][category] = rp_budget * weight

    return LeagueBudget(
        total=total,
        hitter_budget=hitter_budget,
        pitcher_budget=pitcher_budget,
        sp_budget=sp_budget,
        rp_budget=rp_budget,
        category_budgets=category_budgets,
    )


def allocate_position_budgets(
    pools: dict[str, PositionPool],
    league_budget: LeagueBudget,
    budget_config: dict[str, Any],
) -> dict[str, PositionPool]:
    """
    Allocate category budgets to each position based on production share.
    Counting stats by actual production, rate stats by weighted PA.
    """
    # Separate counting and rate stats
    counting_stats = ["R", "HR", "RBI", "SBN"]

    # Calculate total production across all pools
    total_production: dict[str, float] = {}
    for category in counting_stats:
        total_production[category] = sum(
            sum(get_player_stat(player, category) for player in pool.rostered_players)
            for _, pool in pools.items()
        )

    # Calculate total weighted PA for rate stats
    pool_weighted_opb: dict[str, float] = {}
    pool_weighted_slg: dict[str, float] = {}
    total_weighted_obp = 0.0
    total_weighted_slg = 0.0
    for pos, pool in pools.items():
        pa_weight = budget_config["pa_weights"].get(
            pos, budget_config["pa_weights"]["default"]
        )
        pool_obp = statistics.mean(
            [get_player_stat(player, "OBP") for player in pool.rostered_players]
        )
        pool_slg = statistics.mean(
            [get_player_stat(player, "SLG") for player in pool.rostered_players]
        )
        pool_weighted_opb[pos] = pool_obp * pa_weight
        pool_weighted_slg[pos] = pool_slg * pa_weight
        total_weighted_obp += pool_weighted_opb[pos]
        total_weighted_slg += pool_weighted_slg[pos]

    total_production["OBP"] = total_weighted_obp
    total_production["SLG"] = total_weighted_slg

    # Allocate to each position
    for pos, pool in pools.items():
        # Counting stats: by production share
        for category in counting_stats:
            pool_production = sum(
                get_player_stat(player, category) for player in pool.rostered_players
            )
            pool.production_share[category] = (
                pool_production / total_production[category]
            )
            pool.category_budgets[category] = (
                league_budget.category_budgets["hitter"][category]
                * pool.production_share[category]
            )

        # Rate stats: by PA share
        pool.production_share["OBP"] = pool_weighted_opb[pos] / total_weighted_obp
        pool.production_share["SLG"] = pool_weighted_slg[pos] / total_weighted_slg
        pool.category_budgets["OBP"] = (
            league_budget.category_budgets["hitter"]["OBP"]
            * pool.production_share["OBP"]
        )
        pool.category_budgets["SLG"] = (
            league_budget.category_budgets["hitter"]["SLG"]
            * pool.production_share["SLG"]
        )

    return pools


def allocate_pool_budget(
    pool: PositionPool,
    total_budget: float,
    category_weights: dict[str, float],
) -> PositionPool:
    """Allocate budget for a single pitcher pool (SP or RP)."""
    pool.category_budgets = {}

    for category, weight in category_weights.items():
        pool.category_budgets[category] = total_budget * weight

    return pool


def _rostered_category_z(pool: PositionPool, category: str) -> float:
    """Sum the settled z of a pool's rostered tier in one category.

    Prefer ``valuations_by_position[pool.position]`` when present so
    cross-pool players (e.g. a UTIL replacement-tier player who's also in
    1B's replacement tier) get the right pool's z-score for THIS pool's
    $/Z calibration, even if a later swap-pass refreshed their top-level
    ``normalized_z`` for another pool.
    """
    total = 0.0
    for player in pool.rostered_players:
        pv = player.valuation.valuations_by_position.get(pool.position)
        if pv is not None and pv.normalized_z:
            total += pv.normalized_z.get(category, 0.0)
        else:
            total += player.valuation.normalized_z.get(category, 0.0)
    return total


def calc_pool_dollars_per_z(pools: dict[str, PositionPool]) -> dict[str, PositionPool]:
    """Calculate the $/Z conversion rate for each position-category.

    Path B (settled-z) contract: each pool's per-cat z-scores already live
    on the player, written by the iteration loop.

    The conditional baseline shift (``iteration.py`` Step 3b) guarantees a
    POSITIVE rostered Σz for every category: wherever the replacement
    archetype would leave ``Σ(raw z) <= 0`` it re-baselines that category
    to the worst rostered player. So ``$/Z = budget / Σz`` is always
    well-defined. The guard below is a tripwire — a non-positive Σz means
    that upstream invariant has broken; fail loud rather than mis-allocate.
    """
    for pool in pools.values():
        pool.dollars_per_z = {}
        pool.total_pool_z = {}

        for category in pool.category_budgets.keys():
            pool_cat_total_z = _rostered_category_z(pool, category)
            pool.total_pool_z[category] = pool_cat_total_z

            if pool_cat_total_z <= 0:
                raise ValueError(
                    f"calc_pool_dollars_per_z: {category} in "
                    f"{pool.position} has rostered Σz={pool_cat_total_z:.4f} "
                    f"<= 0. The conditional baseline shift should guarantee "
                    f"Σz > 0 — an upstream invariant has broken."
                )
            pool.dollars_per_z[category] = (
                pool.category_budgets[category] / pool_cat_total_z
            )

    return pools
