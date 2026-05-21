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
    """
    Calculate the $/Z conversion rate for each position-category.

    Path B (settled-z) contract: each pool's per-cat z-scores already live
    on the player, written by the iteration loop.

    Signed z (no clamp) means a category's rostered Σz can land <= 0 — the
    rostered tier produces nothing above the replacement archetype there.
    A non-positive Σz cannot absorb a budget. Rather than drop that budget
    (money would vanish from the pool) or hard-fail, its dollars are
    REALLOCATED to the pool's live categories (Σz > 0), proportional to
    their budgets. The pool's total budget is conserved; the dead category
    simply settles to $/Z = 0. Only a pool with NO live category at all
    (every category <= 0) is unrecoverable and raises.
    """
    for pool in pools.values():
        pool.dollars_per_z = {}
        pool.total_pool_z = {}
        categories = list(pool.category_budgets.keys())

        # Pass 1: settled-z sum per category.
        for category in categories:
            pool.total_pool_z[category] = _rostered_category_z(pool, category)

        # Pass 2: reallocate the budget of any non-positive-Σz category to
        # the pool's live categories so no money vanishes from the pool.
        live = [c for c in categories if pool.total_pool_z[c] > 0]
        dead = [c for c in categories if pool.total_pool_z[c] <= 0]
        orphan = sum(pool.category_budgets.get(c, 0.0) for c in dead)
        if orphan > 1e-9:
            if not live:
                raise ValueError(
                    f"calc_pool_dollars_per_z: {pool.position} has a "
                    f"positive budget (${orphan:.2f}) but every category's "
                    f"rostered Σz is <= 0 — no live category to absorb it. "
                    f"The pool's budget cannot be distributed."
                )
            live_budget_total = sum(pool.category_budgets[c] for c in live)
            for c in live:
                share = (
                    pool.category_budgets[c] / live_budget_total
                    if live_budget_total > 0
                    else 1.0 / len(live)
                )
                pool.category_budgets[c] += orphan * share
            for c in dead:
                pool.category_budgets[c] = 0.0

        # Pass 3: $/Z. Dead categories carry no production -> $/Z = 0
        # (their budget was moved to the live categories in Pass 2).
        for category in categories:
            total_z = pool.total_pool_z[category]
            if total_z <= 0:
                pool.dollars_per_z[category] = 0.0
                continue
            pool.dollars_per_z[category] = (
                pool.category_budgets[category] / total_z
            )

    return pools
