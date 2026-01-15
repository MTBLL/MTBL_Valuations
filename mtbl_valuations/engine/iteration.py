"""Iteration to convergence logic for TRP system."""

from __future__ import annotations

from typing import Any

from ..domain.models import PositionPool
from .pools import rebuild_replacement_tier
from .valuation import (
    calc_raw_z,
    calc_normalized_z,
    get_categories,
)


def iterate_to_convergence(
    pools: list[PositionPool],
    budget_config: dict[str, Any],
    league_settings: dict[str, Any],
    composite_rlp_archetype: dict[str, float] | None = None,
) -> list[PositionPool]:
    """
    Iterate until tier membership stabilizes.
    Recalculates Z-scores based on rostered tier, re-ranks, and reassigns tiers.

    composite_rlp_archetype: Optional dict of RAW STATS representing composite RLP
        (e.g., {'HR': 18.0, 'R': 65.0, ...}). If provided, uses this instead of
        pool's own RLP tier to calculate baseline z-scores. Used for UTIL pool.
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
                player.computed.raw_z = calc_raw_z(player, pool, categories)

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
                player.computed.normalized_z = calc_normalized_z(player, pool)
                player.computed.total_z = sum(player.computed.normalized_z.values())

            # Step 5: Re-rank by total Z
            all_pool_players = sorted(
                all_pool_players, key=lambda p: p.computed.total_z, reverse=True
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
                all_pool_players, pool, budget_config
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
                player.computed.tier = "ROSTERED"
            for player in pool.replacement_players:
                player.computed.tier = "REPLACEMENT"
            for player in pool.below_replacement:
                player.computed.tier = "BELOW_REPLACEMENT"

        # Check convergence
        if changes <= convergence_threshold:
            print(f"Converged after {iteration} iterations")
            break
    else:
        print(f"Max iterations ({max_iterations}) reached")

    return pools
