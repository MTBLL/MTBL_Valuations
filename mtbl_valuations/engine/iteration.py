"""Iteration to convergence logic for TRP system."""

from __future__ import annotations

import statistics
from typing import Any, Iterable

from mtbl_valuations.domain.models import Player, PositionPool, PositionValuation
from mtbl_valuations.engine.iteration_logger import current_logger, current_phase
from mtbl_valuations.engine.pools import rebuild_replacement_tier_on_z
from mtbl_valuations.engine.valuation import (
    get_categories,
    get_player_stat,
)


def iterate_to_convergence_global(
    pools: dict[str, PositionPool],
    budget_config: dict[str, Any],
    league_settings: dict[str, Any],
    composite_rlp_archetype: dict[str, float] | None = None,
) -> dict[str, PositionPool]:
    """Iterate until tier membership stabilizes (single-position mode).

    Used after final convergence when each player is assigned to exactly
    one position. Stores z-scores and tier directly on player.valuation.

    Args:
        pools: Position pools to iterate.
        budget_config: Configuration with max_iterations and convergence_threshold.
        league_settings: League configuration including scoring categories.
        composite_rlp_archetype: Optional dict of RAW STATS representing composite RLP
            (e.g., {'HR': 18.0, 'R': 65.0, ...}). If provided, uses this instead of
            pool's own RLP tier to calculate baseline z-scores. Used for UTIL pool.
    """
    max_iterations = budget_config["max_iterations"]
    convergence_threshold = budget_config["convergence_threshold"]
    converged = False
    iteration = 0

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

                _store_z_scores_global(player, z_by_cat)

            # Step 5: Re-rank by total Z
            all_pool_players = sorted(
                all_pool_players, key=lambda p: p.valuation.total_z, reverse=True
            )

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
                use_per_pool_z=False,
            )

            # Update below_replacement
            rostered_and_replacement_ids = {
                p.id for p in pool.rostered_players + pool.replacement_players
            }
            pool.below_replacement = [
                p for p in all_pool_players if p.id not in rostered_and_replacement_ids
            ]

            assign_player_tiers_global(pool)

            # Iteration-log hook (no-op when no logger is bound to context).
            iter_log = current_logger()
            if iter_log is not None:
                iter_log.log_iter(
                    pool,
                    current_phase(),
                    iteration,
                    per_position=False,
                    categories=categories,
                )

        # Check convergence
        if changes <= convergence_threshold:
            print(f"Converged after {iteration} iterations")
            converged = True
            break
    else:
        print(f"Max iterations ({max_iterations}) reached")

    iter_log = current_logger()
    if iter_log is not None:
        for pos in pools:
            iter_log.log_converged(
                current_phase(), pos, iteration, converged, max_iterations
            )

    return pools


def iterate_to_convergence_per_position(
    pools: dict[str, PositionPool],
    budget_config: dict[str, Any],
    league_settings: dict[str, Any],
    composite_rlp_archetype: dict[str, float] | None = None,
) -> dict[str, PositionPool]:
    """Iterate until tier membership stabilizes (multi-position mode).

    Used during multi-eligibility iteration when players can be in multiple
    position pools. Stores z-scores and tier in player.valuation.valuations_by_position[pos].

    Args:
        pools: Position pools to iterate.
        budget_config: Configuration with max_iterations and convergence_threshold.
        league_settings: League configuration including scoring categories.
        composite_rlp_archetype: Optional dict of RAW STATS representing composite RLP
            (e.g., {'HR': 18.0, 'R': 65.0, ...}). If provided, uses this instead of
            pool's own RLP tier to calculate baseline z-scores. Used for UTIL pool.
    """
    max_iterations = budget_config["max_iterations"]
    convergence_threshold = budget_config["convergence_threshold"]
    converged = False
    iteration = 0

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

                _store_z_scores_per_position(player, pos, z_by_cat)

            # Step 5: Re-rank by total Z
            all_pool_players = sorted(
                all_pool_players,
                key=lambda p: p.valuation.valuations_by_position[pos].total_z,
                reverse=True,
            )

            # Store position rank for each player
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
                use_per_pool_z=True,
            )

            # Update below_replacement
            rostered_and_replacement_ids = {
                p.id for p in pool.rostered_players + pool.replacement_players
            }
            pool.below_replacement = [
                p for p in all_pool_players if p.id not in rostered_and_replacement_ids
            ]

            assign_player_tiers_per_position(pool)

            # Iteration-log hook (no-op when no logger is bound to context).
            iter_log = current_logger()
            if iter_log is not None:
                iter_log.log_iter(
                    pool,
                    current_phase(),
                    iteration,
                    per_position=True,
                    categories=categories,
                )

        # Check convergence
        if changes <= convergence_threshold:
            print(f"Converged after {iteration} iterations")
            converged = True
            break
    else:
        print(f"Max iterations ({max_iterations}) reached")

    iter_log = current_logger()
    if iter_log is not None:
        for pos in pools:
            iter_log.log_converged(
                current_phase(), pos, iteration, converged, max_iterations
            )

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


def assign_player_tiers_global(pool: PositionPool) -> None:
    """Assign tiers at top-level player.valuation (single-position mode).

    Used after final convergence when each player is assigned to exactly
    one position. Stores tier directly on player.valuation.tier.

    Args:
        pool: Position pool with players in rostered/replacement/below tiers
    """
    for player in pool.rostered_players:
        player.valuation.tier = "ROSTERED"
    for player in pool.replacement_players:
        player.valuation.tier = "REPLACEMENT"
    for player in pool.below_replacement:
        player.valuation.tier = "BELOW_REPLACEMENT"


def finalize_pool_player_valuations(pool: PositionPool) -> None:
    """Finalize player valuations for a single-position pool.

    Updates all player.valuation fields to reflect their assignment to this pool:
    - Sets primary_position to the pool's position
    - Copies per-position Z-scores to top-level (normalized_z, total_z)
    - Assigns tier based on pool tier membership (ROSTERED, REPLACEMENT, BELOW_REPLACEMENT)

    Used after per-position iteration when transitioning from multi-position tracking
    to single-position assignments (e.g., UTIL pool in Phase 4c, pitcher pools in Phase 8).

    Args:
        pool: Position pool with converged valuations and finalized membership
    """
    position = pool.position

    # Update all rostered players
    for player in pool.rostered_players:
        player.valuation.primary_position = position
        player.valuation.tier = "ROSTERED"

        # Copy per-position Z-scores to top-level if available
        if position in player.valuation.valuations_by_position:
            pos_val = player.valuation.valuations_by_position[position]
            player.valuation.normalized_z = pos_val.normalized_z
            player.valuation.total_z = pos_val.total_z

    # Update all replacement players
    for player in pool.replacement_players:
        player.valuation.primary_position = position
        player.valuation.tier = "REPLACEMENT"

        if position in player.valuation.valuations_by_position:
            pos_val = player.valuation.valuations_by_position[position]
            player.valuation.normalized_z = pos_val.normalized_z
            player.valuation.total_z = pos_val.total_z

    # Update all below-replacement players
    for player in pool.below_replacement:
        player.valuation.primary_position = position
        player.valuation.tier = "BELOW_REPLACEMENT"

        if position in player.valuation.valuations_by_position:
            pos_val = player.valuation.valuations_by_position[position]
            player.valuation.normalized_z = pos_val.normalized_z
            player.valuation.total_z = pos_val.total_z


def sync_pool_z_to_position(pools: dict[str, PositionPool]) -> None:
    """Mirror each pool's top-level Z-scores / tier into valuations_by_position.

    ``iterate_to_convergence_global`` writes fresh scores to the top-level
    ``player.valuation`` fields, but ``valuations_by_position`` still holds the
    stale multi-position scores from the earlier per-position pass. Phase 5
    reads both: ``calc_pool_dollars_per_z`` derives ``$/Z`` from the top-level
    scores while ``distribute_player_dollars(store_in_position_valuation=True)``
    applies that rate to the per-position scores. If the two diverge, the
    per-position dollars no longer sum to the pool's category budget and the
    detailed per-position exports misstate dollar values.

    This is only meaningful for the hitter pools, which are the ones
    distributed with ``store_per_position=True``. Pitcher pools distribute
    from the top-level scores and intentionally leave ``valuations_by_position``
    empty so the exports fall back to those top-level values.
    """
    for pos, pool in pools.items():
        for player in (
            pool.rostered_players + pool.replacement_players + pool.below_replacement
        ):
            _ensure_position_valuation(player, pos)
            pos_val = player.valuation.valuations_by_position[pos]
            pos_val.normalized_z = player.valuation.normalized_z
            pos_val.total_z = player.valuation.total_z
            pos_val.tier = player.valuation.tier


def assign_player_tiers_per_position(pool: PositionPool) -> None:
    """Assign tiers in valuations_by_position (multi-position mode).

    Used during multi-eligibility iteration when players can be in multiple
    position pools. Stores tier in player.valuation.valuations_by_position[pos].

    Args:
        pool: Position pool with players in rostered/replacement/below tiers
    """
    pos = pool.position
    for player in pool.rostered_players:
        player.valuation.valuations_by_position[pos].tier = "ROSTERED"
    for player in pool.replacement_players:
        player.valuation.valuations_by_position[pos].tier = "REPLACEMENT"
    for player in pool.below_replacement:
        player.valuation.valuations_by_position[pos].tier = "BELOW_REPLACEMENT"


def _store_z_scores_global(player: Player, normalized_z: dict[str, float]) -> None:
    """Store z-scores at top-level player.valuation (single-position mode).

    Args:
        player: Player to store z-scores for
        normalized_z: Dictionary of category z-scores
    """
    total_z = sum(normalized_z.values())
    player.valuation.normalized_z = normalized_z
    player.valuation.total_z = total_z


def _store_z_scores_per_position(
    player: Player, pos: str, normalized_z: dict[str, float]
) -> None:
    """Store z-scores in valuations_by_position (multi-position mode).

    Args:
        player: Player to store z-scores for
        pos: Position to store z-scores for
        normalized_z: Dictionary of category z-scores
    """
    total_z = sum(normalized_z.values())
    _ensure_position_valuation(player, pos)
    player.valuation.valuations_by_position[pos].normalized_z = normalized_z
    player.valuation.valuations_by_position[pos].total_z = total_z


def _safe_mean(nums: Iterable[float]) -> float:
    nums = list(nums)
    return statistics.mean(nums) if nums else 0.0


def _safe_stdev(nums: Iterable[float]) -> float:
    nums = list(nums)
    # statistics.stdev requires at least 2 points; decide what you want otherwise
    return statistics.stdev(nums) if len(nums) >= 2 else 0.0
