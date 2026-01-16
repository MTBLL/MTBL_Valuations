"""Budget calculation and allocation functions."""

from __future__ import annotations

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
    rate_stats = ["OBP", "SLG"]

    # Calculate total production across all pools
    total_production: dict[str, float] = {}
    for category in counting_stats:
        total_production[category] = sum(
            sum(get_player_stat(player, category) for player in pool.rostered_players)
            for _, pool in pools.items()
        )

    # Calculate total weighted PA for rate stats
    total_weighted_pa = 0.0
    for pos, pool in pools.items():
        pa_weight = budget_config["pa_weights"].get(
            pos, budget_config["pa_weights"]["default"]
        )
        pool_pa = len(pool.rostered_players) * pa_weight
        pool.weighted_pa = pool_pa
        total_weighted_pa += pool_pa

    # Allocate to each position
    for pos, pool in pools.items():
        pool.category_budgets = {}
        pool.production_share = {}

        # Counting stats: by production share
        for category in counting_stats:
            pool_production = sum(
                get_player_stat(player, category) for player in pool.rostered_players
            )
            if total_production.get(category, 0) > 0:
                pool.production_share[category] = (
                    pool_production / total_production[category]
                )
                pool.category_budgets[category] = (
                    league_budget.category_budgets["hitter"][category]
                    * pool.production_share[category]
                )
            else:
                pool.production_share[category] = 0.0
                pool.category_budgets[category] = 0.0

        # Rate stats: by PA share
        for category in rate_stats:
            if total_weighted_pa > 0:
                pool.production_share[category] = pool.weighted_pa / total_weighted_pa
                pool.category_budgets[category] = (
                    league_budget.category_budgets["hitter"][category]
                    * pool.production_share[category]
                )
            else:
                pool.production_share[category] = 0.0
                pool.category_budgets[category] = 0.0

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


def calc_dollars_per_z(pools: dict[str, PositionPool]) -> dict[str, PositionPool]:
    """Calculate $/Z conversion rate for each position-category."""
    for pool in pools.values():
        pool.dollars_per_z = {}
        pool.total_pool_z = {}

        for category in pool.category_budgets.keys():
            # Sum of positive Z-scores in rostered tier
            total_z = sum(
                max(0.0, player.computed.normalized_z.get(category, 0.0))
                for player in pool.rostered_players
            )

            pool.total_pool_z[category] = total_z

            if total_z > 0:
                pool.dollars_per_z[category] = pool.category_budgets[category] / total_z
            else:
                pool.dollars_per_z[category] = 0.0

    return pools
