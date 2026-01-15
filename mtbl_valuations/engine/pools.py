"""Position pool building and tier assignment functions."""

from __future__ import annotations

from typing import Any

from ..domain.models import Player, PositionPool, Role
from .valuation import get_composite_metric


def assign_primary_positions(
    players: list[Player],
    roster_slots: dict[str, int],
    num_teams: int,
    role: Role,
) -> list[Player]:
    """
    Assign each player to their most valuable (scarcest) position.
    Processes positions from scarcest to deepest.
    """
    # Get positions for this role
    if role == "HITTER":
        position_names = ["C", "1B", "2B", "3B", "SS", "OF", "DH"]
    elif role == "SP":
        position_names = ["SP"]
    else:  # RP
        position_names = ["RP"]

    # Filter to positions that exist in roster_slots
    valid_positions = [p for p in position_names if p in roster_slots]

    # Sort positions by scarcity (fewest roster slots first)
    position_order = sorted(valid_positions, key=lambda p: roster_slots[p])

    assigned: dict[str, str] = {}

    for position in position_order:
        # Get players eligible for this position who haven't been assigned
        eligible = [
            p for p in players if position in p.positions and p.id not in assigned
        ]

        # Total slots needed (including replacement buffer)
        slots = roster_slots[position] * num_teams
        buffer_slots = int(slots * 0.5)
        total_needed = slots + buffer_slots

        # Sort by composite metric (wRC+ for hitters, -FIP for pitchers)
        eligible = sorted(eligible, key=get_composite_metric, reverse=True)

        # Assign top N players to this position
        for i in range(min(total_needed, len(eligible))):
            assigned[eligible[i].id] = position
            eligible[i].computed.primary_position = position

    return players


def build_position_pools(
    players: list[Player],
    roster_slots: dict[str, int],
    num_teams: int,
    role: Role,
    budget_config: dict[str, Any],
) -> list[PositionPool]:
    """Build initial position pools with rostered and replacement tiers."""
    # Get positions for this role
    if role == "HITTER":
        position_names = ["C", "1B", "2B", "3B", "SS", "OF"]
    elif role == "SP":
        position_names = ["SP"]
    else:  # RP
        position_names = ["RP"]

    # Filter to positions that exist in roster_slots
    valid_positions = [p for p in position_names if p in roster_slots]

    pools: list[PositionPool] = []

    for position in valid_positions:
        pool = PositionPool(
            position=position,
            role=role,
            roster_slots=roster_slots[position] * num_teams,
        )

        # Get players assigned to this position
        position_players = [
            p for p in players if p.computed.primary_position == position
        ]

        # Sort by composite metric
        position_players = sorted(
            position_players, key=get_composite_metric, reverse=True
        )

        # Initial tier assignment
        pool.rostered_players = position_players[: pool.roster_slots]

        # Replacement tier: within X% of last rostered player
        if pool.rostered_players:
            last_rostered_metric = get_composite_metric(pool.rostered_players[-1])
            threshold = last_rostered_metric * (
                1 - budget_config["replacement_tier_pct"]
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

            # Everything else is below replacement
            pool.below_replacement = position_players[
                pool.roster_slots + len(pool.replacement_players) :
            ]

        pools.append(pool)

    return pools


def build_single_pool(
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
    sorted_players = sorted(players, key=get_composite_metric, reverse=True)

    # Initial tier assignment
    pool.rostered_players = sorted_players[: pool.roster_slots]

    # Replacement tier
    if pool.rostered_players:
        last_rostered_metric = get_composite_metric(pool.rostered_players[-1])
        threshold = last_rostered_metric * (1 - budget_config["replacement_tier_pct"])

        replacement_candidates = [
            p
            for p in sorted_players[pool.roster_slots :]
            if get_composite_metric(p) >= threshold
        ]

        # Enforce minimum tier size
        min_size = budget_config["min_replacement_tier_size"]
        if len(replacement_candidates) < min_size:
            replacement_candidates = sorted_players[
                pool.roster_slots : pool.roster_slots + min_size
            ]

        pool.replacement_players = replacement_candidates
        pool.below_replacement = sorted_players[
            pool.roster_slots + len(pool.replacement_players) :
        ]

    return pool


def build_util_pool(
    hitter_pools: list[PositionPool],
    pure_dh_players: list[Player],
    roster_slots: dict[str, int],
    num_teams: int,
    budget_config: dict[str, Any],
) -> PositionPool:
    """
    Build UTIL pool from replacement-tier players across all positions.
    This must happen AFTER position pools converge.
    """
    pool = PositionPool(
        position="UTIL",
        role="HITTER",
        roster_slots=roster_slots.get("UTIL", 0) * num_teams,
    )

    # Collect all replacement-tier and below-replacement players
    util_candidates: list[Player] = []
    seen_ids: set[str] = set()

    for position_pool in hitter_pools:
        for player in (
            position_pool.replacement_players + position_pool.below_replacement
        ):
            if player.id not in seen_ids:
                util_candidates.append(player)
                seen_ids.add(player.id)

    # Add pure DH players
    for player in pure_dh_players:
        if player.id not in seen_ids:
            util_candidates.append(player)
            seen_ids.add(player.id)

    # Sort by composite metric
    util_candidates = sorted(util_candidates, key=get_composite_metric, reverse=True)

    # Initial tier assignment
    pool.rostered_players = util_candidates[: pool.roster_slots]

    # Replacement tier
    if pool.rostered_players:
        last_rostered_metric = get_composite_metric(pool.rostered_players[-1])
        threshold = last_rostered_metric * (1 - budget_config["replacement_tier_pct"])

        replacement_candidates = [
            p
            for p in util_candidates[pool.roster_slots :]
            if get_composite_metric(p) >= threshold
        ]

        # Enforce minimum tier size
        min_size = budget_config["min_replacement_tier_size"]
        if len(replacement_candidates) < min_size:
            replacement_candidates = util_candidates[
                pool.roster_slots : pool.roster_slots + min_size
            ]

        pool.replacement_players = replacement_candidates
        pool.below_replacement = util_candidates[
            pool.roster_slots + len(pool.replacement_players) :
        ]

    return pool


def rebuild_replacement_tier(
    all_pool_players: list[Player],
    pool: PositionPool,
    budget_config: dict[str, Any],
) -> list[Player]:
    """Rebuild replacement tier after re-ranking by total Z."""
    if not pool.rostered_players:
        return []

    # Get players after rostered tier
    remaining = all_pool_players[pool.roster_slots :]

    # Use total_z instead of composite metric for threshold
    if pool.rostered_players:
        last_rostered_z = pool.rostered_players[-1].computed.total_z
        threshold = last_rostered_z * (1 - budget_config["replacement_tier_pct"])

        replacement_candidates = [
            p for p in remaining if p.computed.total_z >= threshold
        ]

        # Enforce minimum tier size
        min_size = budget_config["min_replacement_tier_size"]
        if len(replacement_candidates) < min_size and len(remaining) >= min_size:
            replacement_candidates = remaining[:min_size]

        return replacement_candidates

    return []
