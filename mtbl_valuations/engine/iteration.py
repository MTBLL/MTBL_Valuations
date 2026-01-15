"""Iteration to convergence logic for TRP system."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..domain.models import Player, PositionPool, PositionValuation

if TYPE_CHECKING:
    from ..domain.models import LeagueBudget
from .pools import rebuild_replacement_tier
from .valuation import (
    calc_raw_z,
    calc_normalized_z,
    get_categories,
)


def _ensure_position_valuation(player: Player, position: str) -> None:
    """Ensure a PositionValuation exists for this position."""
    if position not in player.computed.valuations_by_position:
        player.computed.valuations_by_position[position] = PositionValuation(
            position=position,
            raw_z={},
            normalized_z={},
            dollar_values={},
            total_z=0.0,
            total_dollars=0.0,
            tier="BELOW_REPLACEMENT",
        )


def iterate_to_convergence(
    pools: list[PositionPool],
    budget_config: dict[str, Any],
    league_settings: dict[str, Any],
    composite_rlp_archetype: dict[str, float] | None = None,
    track_per_pool: bool = False,
) -> list[PositionPool]:
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

        for pool in pools:
            # Get categories for this pool's role
            categories = get_categories(pool.role, league_settings)

            # Step 1: Calculate rostered tier mean and stdev per category
            pool.rostered_tier_means = {}
            pool.rostered_tier_stdevs = {}

            for category in categories:
                values = []
                for player in pool.rostered_players:
                    if hasattr(player, "stats"):
                        from .valuation import get_player_stat

                        val = get_player_stat(player, category)
                        values.append(val)

                if values:
                    pool.rostered_tier_means[category] = sum(values) / len(values)

                    if len(values) > 1:
                        mean = pool.rostered_tier_means[category]
                        variance = sum((v - mean) ** 2 for v in values) / len(values)
                        pool.rostered_tier_stdevs[category] = variance**0.5
                    else:
                        pool.rostered_tier_stdevs[category] = 1.0
                else:
                    pool.rostered_tier_means[category] = 0.0
                    pool.rostered_tier_stdevs[category] = 1.0

            # Step 2: Calculate raw Z-scores for all players
            all_pool_players = (
                pool.rostered_players
                + pool.replacement_players
                + pool.below_replacement
            )

            for player in all_pool_players:
                raw_z = calc_raw_z(player, pool, categories)
                if track_per_pool:
                    _ensure_position_valuation(player, pool.position)
                    player.computed.valuations_by_position[pool.position].raw_z = raw_z
                else:
                    player.computed.raw_z = raw_z

            # Step 3: Calculate RLP average raw Z (the baseline shift)
            pool.rlp_raw_z_avg = {}
            if composite_rlp_archetype is not None:
                # Use composite RLP archetype (for UTIL)
                from .valuation import calc_z_scores_for_archetype

                pool.rlp_raw_z_avg = calc_z_scores_for_archetype(
                    composite_rlp_archetype, pool.rostered_players
                )
            elif pool.replacement_players:
                # Use pool's own RLP tier (normal case)
                for category in categories:
                    if track_per_pool:
                        rlp_values = [
                            p.computed.valuations_by_position[pool.position].raw_z.get(
                                category, 0.0
                            )
                            for p in pool.replacement_players
                        ]
                    else:
                        rlp_values = [
                            p.computed.raw_z.get(category, 0.0)
                            for p in pool.replacement_players
                        ]
                    if rlp_values:
                        pool.rlp_raw_z_avg[category] = sum(rlp_values) / len(
                            rlp_values
                        )
                    else:
                        pool.rlp_raw_z_avg[category] = 0.0
            else:
                for category in categories:
                    pool.rlp_raw_z_avg[category] = 0.0

            # Step 4: Normalize Z-scores (subtract RLP average)
            for player in all_pool_players:
                if track_per_pool:
                    raw_z = player.computed.valuations_by_position[pool.position].raw_z
                    normalized_z = {
                        cat: raw_z.get(cat, 0.0) - pool.rlp_raw_z_avg.get(cat, 0.0)
                        for cat in categories
                    }
                    player.computed.valuations_by_position[
                        pool.position
                    ].normalized_z = normalized_z
                    player.computed.valuations_by_position[
                        pool.position
                    ].total_z = sum(normalized_z.values())
                else:
                    player.computed.normalized_z = calc_normalized_z(player, pool)
                    player.computed.total_z = sum(
                        player.computed.normalized_z.values()
                    )

            # Step 5: Re-rank by total Z
            def get_total_z(p: Player) -> float:
                if track_per_pool:
                    return p.computed.valuations_by_position[pool.position].total_z
                return p.computed.total_z

            all_pool_players = sorted(
                all_pool_players, key=get_total_z, reverse=True
            )

            # Step 6: Reassign tiers based on new ranking
            new_rostered = all_pool_players[: pool.roster_slots]

            # Check for changes
            old_ids = {player.id for player in pool.rostered_players}
            new_ids = {player.id for player in new_rostered}
            if old_ids != new_ids:
                changes += 1

            # Update tiers
            pool.rostered_players = new_rostered
            pool.replacement_players = rebuild_replacement_tier(
                all_pool_players,
                pool,
                budget_config,
                use_per_pool_z=track_per_pool,
            )

            # Update below_replacement
            rostered_and_replacement_ids = {
                p.id for p in pool.rostered_players + pool.replacement_players
            }
            pool.below_replacement = [
                p for p in all_pool_players if p.id not in rostered_and_replacement_ids
            ]

            # Mark player tiers
            for player in pool.rostered_players:
                if track_per_pool:
                    player.computed.valuations_by_position[
                        pool.position
                    ].tier = "ROSTERED"
                else:
                    player.computed.tier = "ROSTERED"
            for player in pool.replacement_players:
                if track_per_pool:
                    player.computed.valuations_by_position[
                        pool.position
                    ].tier = "REPLACEMENT"
                else:
                    player.computed.tier = "REPLACEMENT"
            for player in pool.below_replacement:
                if track_per_pool:
                    player.computed.valuations_by_position[
                        pool.position
                    ].tier = "BELOW_REPLACEMENT"
                else:
                    player.computed.tier = "BELOW_REPLACEMENT"

        # Check convergence
        if changes <= convergence_threshold:
            print(f"Converged after {iteration} iterations")
            break
    else:
        print(f"Max iterations ({max_iterations}) reached")

    return pools


def stabilize_position_assignments(
    pools: list[PositionPool],
    all_players: list[Player],
    budget_config: dict[str, Any],
    league_settings: dict[str, Any],
    league_budget: LeagueBudget,
    max_stability_iterations: int = 10,
) -> list[PositionPool]:
    """
    Iterate position assignments until no player would benefit from changing.

    This function implements the stability loop:
    1. Calculate dollar values at each position
    2. Assign each player to highest-value position
    3. Rebuild pools (remove from non-primary pools)
    4. Re-converge pools
    5. Re-calculate dollar values
    6. Check if any player would change - if yes, goto step 2

    Args:
        pools: Position pools that have already converged once with multi-eligibility.
        all_players: All players across all pools.
        budget_config: Configuration for convergence and budget allocation.
        league_settings: League configuration including scoring categories.
        league_budget: League-wide budget structure for dollar allocation.
        max_stability_iterations: Maximum iterations before giving up.

    Returns:
        Stabilized position pools with each player in exactly one pool.
    """
    from .budget import allocate_position_budgets, calc_dollars_per_z
    from .pools import (
        assign_final_positions,
        rebuild_pools_after_assignment,
    )
    from .valuation import calc_player_dollars

    for stability_iter in range(1, max_stability_iterations + 1):
        print(f"\n  Stability iteration {stability_iter}...")

        # Step 1: Calculate dollar values for each position
        pools = allocate_position_budgets(pools, league_budget, budget_config)
        pools = calc_dollars_per_z(pools)

        for pool in pools:
            for player in pool.rostered_players + pool.replacement_players:
                dollar_values = calc_player_dollars(player, pool)
                total_dollars = sum(dollar_values.values())

                # Store in per-position valuation
                if pool.position in player.computed.valuations_by_position:
                    player.computed.valuations_by_position[
                        pool.position
                    ].dollar_values = dollar_values
                    player.computed.valuations_by_position[
                        pool.position
                    ].total_dollars = total_dollars

        # Step 2: Assign to best position
        all_players, changes = assign_final_positions(pools, all_players)
        print(f"    Position changes: {changes}")

        if changes == 0:
            print(f"  Stability achieved after {stability_iter} iterations")
            break

        # Step 3: Rebuild pools (remove players from non-primary positions)
        pools = rebuild_pools_after_assignment(pools)

        # Step 4: Re-converge with single-position mode
        pools = iterate_to_convergence(
            pools,
            budget_config,
            league_settings,
            track_per_pool=False,  # Now single-position
        )
    else:
        print(f"  Max stability iterations ({max_stability_iterations}) reached")

    return pools
