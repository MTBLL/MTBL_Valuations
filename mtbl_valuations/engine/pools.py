"""Position pool building and tier assignment functions."""

from __future__ import annotations

from typing import Any

from ..domain.models import Player, PositionPool, Role
from .valuation import get_composite_metric


def _calc_replacement_threshold(
    last_rostered_metric: float,
    replacement_tier_pct: float,
) -> float:
    """Calculate the replacement tier threshold, handling negative metrics like -FIP.

    For positive metrics (wRC+): threshold is metric * (1 - pct), players >= threshold qualify
    For negative metrics (-FIP): threshold is metric * (1 + pct), players >= threshold qualify

    This ensures that for -FIP, a value like -3.50 qualifies when last rostered is -3.00
    with 3% tolerance (threshold becomes -3.09).
    """
    if last_rostered_metric >= 0:
        # Positive metric (wRC+): lower bound is metric * (1 - pct)
        return last_rostered_metric * (1 - replacement_tier_pct)
    else:
        # Negative metric (-FIP): lower bound is metric * (1 + pct)
        # e.g., -3.00 * 1.03 = -3.09, so -3.50 >= -3.09 is False but -2.90 >= -3.09 is True
        # Wait, that's still wrong. Let me think again...
        # For -FIP, "worse" means more negative. -3.50 is worse than -3.00.
        # We want players within X% of the last rostered player.
        # If last rostered is -3.00 (FIP 3.00), replacement should include -3.09 (FIP 3.09)
        # So threshold = -3.00 * (1 + 0.03) = -3.09
        # Check: -3.05 >= -3.09? Yes (3.05 FIP is replacement level)
        # Check: -3.50 >= -3.09? No (3.50 FIP is too bad)
        return last_rostered_metric * (1 + replacement_tier_pct)


def build_position_pools(
    players: list[Player],
    roster_slots: dict[str, int],
    num_teams: int,
    role: Role,
    budget_config: dict[str, Any],
    use_eligibility: bool = False,
) -> dict[str, PositionPool]:
    """Build initial position pools with rostered and replacement tiers.

    Args:
        players: List of players to assign to pools.
        roster_slots: Dict mapping position to number of roster slots per team.
        num_teams: Number of teams in the league.
        role: The role for these pools (HITTER, SP, or RP).
        budget_config: Configuration with replacement_tier_pct and min_replacement_tier_size.
        use_eligibility: If True, include players in ALL positions they're eligible for
            (based on player.positions). If False, only include players whose
            primary_position matches (post-assignment mode).
    """
    # Get positions for this role
    if role == "HITTER":
        position_names = ["C", "1B", "2B", "3B", "SS", "OF"]
    elif role == "SP":
        position_names = ["SP"]
    else:  # RP
        position_names = ["RP"]

    # Filter to positions that exist in roster_slots
    valid_positions = [p for p in position_names if p in roster_slots]

    pools: dict[str, PositionPool] = {}

    for position in valid_positions:
        pool = PositionPool(
            position=position,
            role=role,
            roster_slots=roster_slots[position] * num_teams,
        )

        # Get players for this position
        if use_eligibility:
            # Include all players eligible for this position
            position_players = [p for p in players if position in p.positions]
        else:
            # Only include players assigned to this position
            position_players = [
                p for p in players if p.valuation.primary_position == position
            ]

        # Sort by composite metric
        position_players = sorted(
            position_players, key=get_composite_metric, reverse=True
        )

        # Initial tier assignment
        pool.rostered_players = position_players[: pool.roster_slots]

        # Replacement tier: within X% of last rostered player
        if pool.rostered_players:
            pool = _build_replacement_tier(pool, position_players, budget_config)

        pools[position] = pool

    return pools


def build_pitcher_pool(
    players: list[Player],
    roster_slots: dict[str, int],
    num_teams: int,
    role: Role,
    budget_config: dict[str, Any],
) -> PositionPool:
    """Build a single pool for SP or RP."""
    position = "SP" if role == "SP" else "RP"

    pool = PositionPool(
        position=position,
        role=role,
        roster_slots=roster_slots.get(position, 0) * num_teams,
    )

    # Sort by composite metric
    sorted_players: list[Player] = sorted(
        players, key=get_composite_metric, reverse=True
    )

    # Initial tier assignment
    pool.rostered_players = sorted_players[: pool.roster_slots]

    # Replacement tier
    if pool.rostered_players:
        pool = _build_replacement_tier(pool, sorted_players, budget_config)

    return pool


def build_util_pool(
    hitter_pools: dict[str, PositionPool],
    pure_dh_players: list[Player],
    roster_slots: dict[str, int],
    num_teams: int,
    budget_config: dict[str, Any],
) -> PositionPool:
    """
    Build UTIL pool from replacement-tier players across all positions.
    This must happen AFTER position pools converge.
    """
    util_pool = PositionPool(
        position="UTIL",
        role="HITTER",
        roster_slots=roster_slots.get("UTIL", 0) * num_teams,
    )

    # Collect all replacement-tier and below-replacement players (dedupe by ID)
    util_candidates: dict[str, Player] = {}

    for position_pool in hitter_pools.values():
        for player in (
            position_pool.replacement_players + position_pool.below_replacement
        ):
            util_candidates[player.id] = player

    # Add pure DH players
    for player in pure_dh_players:
        util_candidates[player.id] = player

    # Sort by composite metric
    util_candidates_sorted: list[Player] = sorted(
        util_candidates.values(), key=get_composite_metric, reverse=True
    )

    # Initial tier assignment
    util_pool.rostered_players = util_candidates_sorted[: util_pool.roster_slots]

    # Replacement tier
    if util_pool.rostered_players:
        util_pool = _build_replacement_tier(
            util_pool, util_candidates_sorted, budget_config
        )

    return util_pool


def _build_replacement_tier(
    pool: PositionPool, position_players: list[Player], budget_config: dict[str, Any]
) -> PositionPool:
    pool = pool
    last_rostered_metric = get_composite_metric(pool.rostered_players[-1])
    threshold = _calc_replacement_threshold(
        last_rostered_metric, budget_config["replacement_tier_pct"]
    )

    replacement_candidates = [
        p
        for p in position_players[pool.roster_slots :]
        if get_composite_metric(p) >= threshold
    ]

    # Enforce minimum tier size
    min_size = budget_config["min_replacement_tier_size"]
    if len(replacement_candidates) < min_size:
        replacement_candidates = position_players[
            pool.roster_slots : pool.roster_slots + min_size
        ]

    pool.replacement_players = replacement_candidates
    pool.below_replacement = position_players[
        pool.roster_slots + len(pool.replacement_players) :
    ]

    return pool


def rebuild_replacement_tier_on_z(
    all_pool_players: list[Player],
    pool: PositionPool,
    budget_config: dict[str, Any],
    use_per_pool_z: bool = False,
) -> list[Player]:
    """Rebuild replacement tier after re-ranking by total Z.

    Args:
        all_pool_players: All players in this pool, sorted by total_z descending.
        pool: The position pool being rebuilt.
        budget_config: Configuration with replacement_tier_pct and min_replacement_tier_size.
        use_per_pool_z: If True, get total_z from player's valuations_by_position dict
            for this pool's position. Use when players appear in multiple pools.
    """
    if not pool.rostered_players:
        return []

    # Get players after rostered tier
    remaining = all_pool_players[pool.roster_slots :]

    def _get_total_z_for_player(p: Player) -> float:
        if use_per_pool_z:
            return p.valuation.valuations_by_position[pool.position].total_z
        return p.valuation.total_z

    # Use total_z instead of composite metric for threshold
    if pool.rostered_players:
        last_rostered_z = _get_total_z_for_player(pool.rostered_players[-1])
        # TODO: perhaps we need create arg for threshold size, or store it in the pool object.
        # a 3% distance from a total z-score typically betwen 0-3 units will never produce a meaningful range
        # it makes sense to have the same RLP size for each iteration. current logic will always enforce the min size
        threshold = last_rostered_z * (1 - budget_config["replacement_tier_pct"])

        replacement_candidates = [
            p for p in remaining if _get_total_z_for_player(p) >= threshold
        ]

        # Enforce minimum tier size
        min_size = budget_config["min_replacement_tier_size"]
        if len(replacement_candidates) < min_size and len(remaining) >= min_size:
            replacement_candidates = remaining[:min_size]

        return replacement_candidates

    return []


def assign_final_positions(
    pools: dict[str, PositionPool],
    players: list[Player],
) -> tuple[list[Player], int]:
    """
    Assign each player to their most valuable position after convergence.

    Examines each player's valuations_by_position dict and assigns them to the
    position where they have the highest dollar value.

    Args:
        pools: Dictionary of position pools (used for reference).
        players: List of all players to assign.

    Returns:
        Tuple of (players with primary_position set, number of position changes).
    """
    changes = 0

    for player in players:
        if not player.valuation.valuations_by_position:
            continue

        # Find position with highest total_z where player is ROSTERED
        # We use total_z (not total_dollars) because dollar values aren't
        # available until all pools have fully stabilized including UTIL
        best_position = None
        best_z = float("-inf")

        for position, valuation in player.valuation.valuations_by_position.items():
            # Prefer positions where player is rostered
            if valuation.tier == "ROSTERED" and valuation.total_z > best_z:
                best_z = valuation.total_z
                best_position = position

        # If not rostered anywhere, fall back to highest total_z regardless of tier
        if best_position is None:
            for position, valuation in player.valuation.valuations_by_position.items():
                if valuation.total_z > best_z:
                    best_z = valuation.total_z
                    best_position = position

        # If still no position (edge case), use first available
        if best_position is None and player.valuation.valuations_by_position:
            best_position = next(iter(player.valuation.valuations_by_position.keys()))

        # Check if position changed
        if best_position and player.valuation.primary_position != best_position:
            changes += 1
            player.valuation.primary_position = best_position

    return players, changes


def rebuild_pools_after_assignment(
    pools: dict[str, PositionPool],
) -> dict[str, PositionPool]:
    """
    Remove players from pools where they are not assigned.

    Called after assign_final_positions() to clean up pools so each player
    only appears in their primary position's pool.

    Args:
        pools: List of position pools to clean up.

    Returns:
        The same pools with non-primary players removed.
    """
    for pos, pool in pools.items():
        # Filter to only players assigned to this position
        pool.rostered_players = [
            p for p in pool.rostered_players if p.valuation.primary_position == pos
        ]
        pool.replacement_players = [
            p for p in pool.replacement_players if p.valuation.primary_position == pos
        ]
        pool.below_replacement = [
            p for p in pool.below_replacement if p.valuation.primary_position == pos
        ]

    return pools


def dedupe_multi_position_players(
    pools: dict[str, PositionPool],
) -> tuple[dict[str, PositionPool], int]:
    """
    Assign multi-position players to their highest-ranked position and remove from others.

    A player ranked #4 at SS and #5 at OF should be assigned to SS (better rank).
    Uses position_rank from valuations_by_position (set during iterate_to_convergence).

    Args:
        pools: Dictionary of position pools with multi-eligible players.

    Returns:
        Tuple of (cleaned pools, number of players reassigned).
    """
    # Collect all unique players across pools
    seen_players: dict[str, Player] = {}
    for pool in pools.values():
        for player in pool.rostered_players:
            seen_players[player.id] = player

    # Assign each player to their best-ranked position
    changes = 0
    for player in seen_players.values():
        valuations = player.valuation.valuations_by_position
        if not valuations:
            continue

        # Find position with best (lowest) rank among ROSTERED positions
        best_pos = None
        best_rank = float("inf")

        for pos, val in valuations.items():
            if val.tier == "ROSTERED" and val.position_rank < best_rank:
                best_rank = val.position_rank
                best_pos = pos

        # Fall back to any position if none are rostered
        if best_pos is None:
            best_pos = min(valuations, key=lambda p: valuations[p].position_rank)

        if player.valuation.primary_position != best_pos:
            changes += 1
            player.valuation.primary_position = best_pos

    # Now remove players from pools where they're not assigned
    pools = rebuild_pools_after_assignment(pools)

    return pools, changes
