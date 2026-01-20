"""Iteration to convergence logic for TRP system."""

from __future__ import annotations

import statistics
from typing import Any, Iterable

from mtbl_valuations.domain.models import Player, PositionPool, PositionValuation
from mtbl_valuations.engine.valuation import get_player_stat

from mtbl_valuations.engine.pools import rebuild_replacement_tier_on_z
from mtbl_valuations.engine.valuation import (
    get_categories,
)


def iterate_to_convergence(
    pools: dict[str, PositionPool],
    budget_config: dict[str, Any],
    league_settings: dict[str, Any],
    composite_rlp_archetype: dict[str, float] | None = None,
    track_z_per_pool: bool = False,
) -> dict[str, PositionPool]:
    """
    Iterate until tier membership stabilizes.
    Recalculates Z-scores based on rostered tier, re-ranks, and reassigns tiers.

    Args:
        pools: Position pools to iterate.
        budget_config: Configuration with max_iterations and convergence_threshold.
        league_settings: League configuration including scoring categories.
        composite_rlp_archetype: Optional dict of RAW STATS representing composite RLP
            (e.g., {'HR': 18.0, 'R': 65.0, ...}). If provided, uses this instead of
            pool's own RLP tier to calculate baseline z-scores. Used for UTIL pool.
        track_per_pool: If True, store Z-scores and tier in player's
            valuations_by_position dict rather than overwriting top-level computed
            values. Use this when players appear in multiple pools simultaneously.
    """
    max_iterations = budget_config["max_iterations"]
    convergence_threshold = budget_config["convergence_threshold"]

    for iteration in range(1, max_iterations + 1):
        changes = 0

        for pos, pool in pools.items():
            categories = get_categories(pool.role, league_settings)

            # Define the player sets once (and as lists, because you loop multiple times)
            rostered = [p for p in pool.rostered_players if hasattr(p, "stats")]
            rlp_tier = [p for p in pool.replacement_players if hasattr(p, "stats")]
            below = [p for p in pool.below_replacement if hasattr(p, "stats")]
            all_pool_players = rostered + rlp_tier + below

            # Step 1: rostered-tier mean & stdev (scale)
            pool.rostered_tier_stdevs = {}
            for cat in categories:
                vals = [get_player_stat(p, cat) for p in rostered]
                pool.rostered_tier_stdevs[cat] = _safe_stdev(vals)

            # Step 2: replacement-tier RAW mean (baseline)
            # composite_rlp_archetype must be a dict[str, float] of raw means per category
            if composite_rlp_archetype is not None:
                pool.rlp_raw_avg = composite_rlp_archetype
            else:
                pool.rlp_raw_avg = {
                    cat: _safe_mean(get_player_stat(p, cat) for p in rlp_tier)
                    for cat in categories
                }

            # Step 3: compute above-replacement z per player + total
            for player in all_pool_players:
                z_by_cat: dict[str, float] = {}
                for cat in categories:
                    x = get_player_stat(player, cat)
                    mu_rlp = pool.rlp_raw_avg.get(cat)
                    assert isinstance(mu_rlp, float), (
                        f"Missing raw mean for category {cat}"
                    )
                    sd = pool.rostered_tier_stdevs.get(cat, 0.0)

                    baseline_delta = (
                        (x - mu_rlp) if cat not in ["ERA", "WHIP"] else (mu_rlp - x)
                    )
                    z_score = baseline_delta / sd if sd else 0.0
                    z_by_cat[cat] = z_score

                _store_z_scores(player, pos, z_by_cat, track_z_per_pool)

            # Step 5: Re-rank by total Z
            def _get_total_z(p: Player) -> float:
                if track_z_per_pool:
                    return p.valuation.valuations_by_position[pos].total_z
                return p.valuation.total_z

            all_pool_players = sorted(all_pool_players, key=_get_total_z, reverse=True)

            # Store position rank for each player
            if track_z_per_pool:
                for rank, player in enumerate(all_pool_players):
                    player.valuation.valuations_by_position[pos].position_rank = rank

            # Step 6: Reassign tiers based on new ranking
            new_rostered_tier = all_pool_players[: pool.roster_slots]

            # Check for changes
            old_ids: set[str] = {player.id for player in pool.rostered_players}
            new_ids: set[str] = {player.id for player in new_rostered_tier}
            if old_ids != new_ids:
                changes += 1

            # Update tiers
            pool.rostered_players = new_rostered_tier
            pool.replacement_players = rebuild_replacement_tier_on_z(
                all_pool_players,
                pool,
                budget_config["replacement_tier_pct"],
                budget_config["min_replacement_tier_size"],
                use_per_pool_z=track_z_per_pool,
            )

            # Update below_replacement
            rostered_and_replacement_ids = {
                p.id for p in pool.rostered_players + pool.replacement_players
            }
            pool.below_replacement = [
                p for p in all_pool_players if p.id not in rostered_and_replacement_ids
            ]

            _assign_player_tiers(pool, track_z_per_pool)

        # Check convergence
        if changes <= convergence_threshold:
            print(f"Converged after {iteration} iterations")
            break
    else:
        print(f"Max iterations ({max_iterations}) reached")

    return pools


### ===     helper functions    === ###


def _ensure_position_valuation(player: Player, position: str) -> None:
    """Ensure a PositionValuation exists for this position."""
    if position not in player.valuation.valuations_by_position:
        player.valuation.valuations_by_position[position] = PositionValuation(
            position=position,
            normalized_z={},
            total_z=0.0,
            tier="BELOW_REPLACEMENT",
            position_rank=100,
        )


def _assign_player_tiers(pool: PositionPool, track_z_per_pool: bool) -> None:
    # Mark player tiers
    for player in pool.rostered_players:
        if track_z_per_pool:
            player.valuation.valuations_by_position[pool.position].tier = "ROSTERED"
        else:
            player.valuation.tier = "ROSTERED"
    for player in pool.replacement_players:
        if track_z_per_pool:
            player.valuation.valuations_by_position[pool.position].tier = "REPLACEMENT"
        else:
            player.valuation.tier = "REPLACEMENT"
    for player in pool.below_replacement:
        if track_z_per_pool:
            player.valuation.valuations_by_position[
                pool.position
            ].tier = "BELOW_REPLACEMENT"
        else:
            player.valuation.tier = "BELOW_REPLACEMENT"


def _store_z_scores(
    player: Player, pos: str, normalized_z: dict[str, float], track_per_pool: bool
) -> None:
    total_z = sum(normalized_z.values())
    if track_per_pool:
        _ensure_position_valuation(player, pos)
        player.valuation.valuations_by_position[pos].normalized_z = normalized_z
        player.valuation.valuations_by_position[pos].total_z = total_z
    else:
        player.valuation.normalized_z = normalized_z
        player.valuation.total_z = total_z


def _get_bucket(player: Player, pos: str, track_per_pool: bool):
    if track_per_pool:
        _ensure_position_valuation(player, pos)
        return player.valuation.valuations_by_position[pos]
    return player.valuation


def _safe_mean(nums: Iterable[float]) -> float:
    nums = list(nums)
    return statistics.mean(nums) if nums else 0.0


def _safe_stdev(nums: Iterable[float]) -> float:
    nums = list(nums)
    # statistics.stdev requires at least 2 points; decide what you want otherwise
    return statistics.stdev(nums) if len(nums) >= 2 else 0.0
