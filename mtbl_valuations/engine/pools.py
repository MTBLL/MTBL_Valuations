"""Position pool building and tier assignment functions."""

from __future__ import annotations

import math
import os

from ..domain.models import Player, PositionPool, Role
from .valuation import get_composite_metric


def _replacement_tier_size(
    roster_slots: int, rlp_tier_pct: float, min_rlp_tier_size: int
) -> int:
    """Replacement-tier player count = a fraction of the pool's rostered
    slots, rounded up, floored at ``min_rlp_tier_size``.

    ``rlp_tier_pct`` is a share of ROSTER SLOTS (not pool size): at 0.5 an
    11-slot pool gets ``ceil(5.5) = 6`` RLPs, a 33-slot pool gets 17. A
    wide, fixed-count tier keeps the replacement baseline (``rlp_raw_avg``)
    stable across iterations — the old 3%-of-z threshold always collapsed
    to ``min_rlp_tier_size``, leaving the baseline a noisy 3-player mean.
    """
    return max(min_rlp_tier_size, math.ceil(roster_slots * rlp_tier_pct))


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
    rlp_tier_pct: float,
    min_rlp_tier_size: int,
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
        _debug(
            f"[build_position_pools] {position}: total_players={len(players)} "
            f"num_teams={num_teams} roster_slots={roster_slots.get(position, 0)}"
        )
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

        _debug(
            f"[build_position_pools] {position}: eligible_players={len(position_players)} "
            f"pool_roster_slots={pool.roster_slots}"
        )

        # Initial tier assignment
        pool.rostered_players = position_players[: pool.roster_slots]

        # Replacement tier: within X% of last rostered player
        if pool.rostered_players:
            pool = _build_replacement_tier(
                pool, position_players, rlp_tier_pct, min_rlp_tier_size
            )
            _debug(
                f"[build_position_pools] {position}: rostered={len(pool.rostered_players)} "
                f"replacement={len(pool.replacement_players)} below={len(pool.below_replacement)}"
            )
        else:
            _debug(f"[build_position_pools] {position}: no rostered players")

        pools[position] = pool

    return pools


def build_pitcher_pool(
    players: list[Player],
    roster_slots: dict[str, int],
    num_teams: int,
    role: Role,
    rlp_tier_pct: float,
    min_rlp_tier_size: int,
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
        pool = _build_replacement_tier(
            pool, sorted_players, rlp_tier_pct, min_rlp_tier_size
        )

    return pool


def build_util_pool(
    hitter_pools: dict[str, PositionPool],
    pure_dh_players: list[Player],
    roster_slots: dict[str, int],
    num_teams: int,
    rlp_tier_pct: float,
    min_rlp_tier_size: int,
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
            util_pool, util_candidates_sorted, rlp_tier_pct, min_rlp_tier_size
        )

    return util_pool


def assign_primary_position_from_pool(
    pool: PositionPool,
) -> None:
    """
    Assign primary_position to all players in a pool based on pool's position.

    Used for:
    - UTIL pool players (Phase 4c)
    - Pitcher pools (Phase 8)
    - Any single-position pool where players should be assigned to the pool's position

    Does NOT assign tiers - use _assign_player_tiers() separately for that.
    """
    for player in pool.rostered_players + pool.replacement_players + pool.below_replacement:
        player.valuation.primary_position = pool.position


def _build_replacement_tier(
    pool: PositionPool,
    position_players: list[Player],
    rlp_tier_pct: float,
    min_rlp_tier_size: int,
) -> PositionPool:
    """Slot the post-rostered players into a fixed-count replacement tier
    (``ceil(roster_slots * rlp_tier_pct)``, see ``_replacement_tier_size``)
    and push the rest to ``below_replacement``. ``position_players`` is
    already sorted best-first by the caller."""
    size = _replacement_tier_size(
        pool.roster_slots, rlp_tier_pct, min_rlp_tier_size
    )
    pool.replacement_players = position_players[
        pool.roster_slots : pool.roster_slots + size
    ]
    pool.below_replacement = position_players[
        pool.roster_slots + len(pool.replacement_players) :
    ]

    if not pool.replacement_players:
        _debug(
            f"[build_replacement_tier] {pool.position}: no remaining players "
            f"(eligible={len(position_players)} roster_slots={pool.roster_slots})"
        )

    return pool


def rebuild_replacement_tier_on_z(
    all_pool_players: list[Player],
    pool: PositionPool,
    rlp_tier_pct: float,
    min_rlp_tier_size: int,
    use_per_pool_z: bool = False,
) -> list[Player]:
    """Rebuild the replacement tier after re-ranking.

    The tier is a fixed count — ``ceil(roster_slots * rlp_tier_pct)``,
    floored at ``min_rlp_tier_size`` (see ``_replacement_tier_size``) —
    taken straight off the top of the post-rostered players.

    Args:
        all_pool_players: All players in this pool, already sorted
            best-first by the caller (every caller does).
        pool: The position pool being rebuilt.
        rlp_tier_pct: Replacement-tier size as a share of roster slots.
        min_rlp_tier_size: Floor on the replacement-tier player count.
        use_per_pool_z: Retained for call-site compatibility. Selection is
            now positional on the caller's sort order, so this no longer
            affects which players are picked.
    """
    if not pool.rostered_players:
        return []

    size = _replacement_tier_size(
        pool.roster_slots, rlp_tier_pct, min_rlp_tier_size
    )
    remaining = all_pool_players[pool.roster_slots :]
    return remaining[:size]


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
        pre_rostered = len(pool.rostered_players)
        pre_replacement = len(pool.replacement_players)
        pre_below = len(pool.below_replacement)

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

        post_rostered = len(pool.rostered_players)
        post_replacement = len(pool.replacement_players)
        post_below = len(pool.below_replacement)

        if _debug_enabled() and (
            pre_rostered != post_rostered
            or pre_replacement != post_replacement
            or pre_below != post_below
        ):
            _debug(
                f"[rebuild_pools_after_assignment] {pos}: "
                f"rostered {pre_rostered}->{post_rostered}, "
                f"replacement {pre_replacement}->{post_replacement}, "
                f"below {pre_below}->{post_below}"
            )

    return pools


def dedupe_multi_position_players(
    pools: dict[str, PositionPool], rlp_tier_pct: float, min_rlp_tier_size: int
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
    # Capture target replacement tier sizes before dedupe/rebuild
    target_replacement_sizes = {
        pos: len(pool.replacement_players) for pos, pool in pools.items()
    }

    # Collect all unique players across pools (rostered + replacement + below)
    seen_players: dict[str, Player] = {}
    for pool in pools.values():
        for player in (
            pool.rostered_players + pool.replacement_players + pool.below_replacement
        ):
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

    _debug(
        f"[dedupe_multi_position_players] reassigned_players={changes} "
        f"unique_players={len(seen_players)}"
    )

    # Now remove players from pools where they're not assigned
    pools = rebuild_pools_after_assignment(pools)
    pools = _refill_tiers_after_dedupe(
        pools, target_replacement_sizes, rlp_tier_pct, min_rlp_tier_size
    )

    # Update tier attributes to match the tier lists after refill
    # Import here to avoid circular dependency (iteration.py imports from pools.py)
    from mtbl_valuations.engine.iteration import assign_player_tiers_per_position

    for pool in pools.values():
        assign_player_tiers_per_position(pool)

    if _debug_enabled():
        for pos, pool in pools.items():
            _debug(
                f"[dedupe_multi_position_players] {pos}: "
                f"rostered={len(pool.rostered_players)}/{pool.roster_slots} "
                f"replacement={len(pool.replacement_players)} "
                f"below={len(pool.below_replacement)}"
            )

    return pools, changes


def _refill_tiers_after_dedupe(
    pools: dict[str, PositionPool],
    target_replacement_sizes: dict[str, int],
    rlp_tier_pct: float,
    min_rlp_tier_size: int,
) -> dict[str, PositionPool]:
    """Slide players up to fill rostered tier, then rebuild replacement tier."""
    for pos, pool in pools.items():
        all_players = (
            pool.rostered_players + pool.replacement_players + pool.below_replacement
        )
        if not all_players:
            continue

        def _get_total_z_for_player(player: Player) -> float:
            valuation = player.valuation.valuations_by_position.get(pos)
            if valuation is None:
                return 0.0
            return valuation.total_z

        all_players_sorted = sorted(
            all_players, key=_get_total_z_for_player, reverse=True
        )

        desired_replacement_size = target_replacement_sizes.get(
            pos, len(pool.replacement_players)
        )

        pre_rostered = len(pool.rostered_players)
        pre_replacement = len(pool.replacement_players)

        pool.rostered_players = all_players_sorted[: pool.roster_slots]
        remaining = all_players_sorted[pool.roster_slots :]

        replacement = rebuild_replacement_tier_on_z(
            all_players_sorted,
            pool,
            rlp_tier_pct,
            min_rlp_tier_size,
            use_per_pool_z=True,
        )
        if len(replacement) < desired_replacement_size:
            seen_ids = {p.id for p in replacement}
            for player in remaining:
                if player.id in seen_ids:
                    continue
                replacement.append(player)
                seen_ids.add(player.id)
                if len(replacement) >= desired_replacement_size:
                    break
        pool.replacement_players = replacement

        replacement_ids = {p.id for p in pool.replacement_players}
        pool.below_replacement = [p for p in remaining if p.id not in replacement_ids]

        post_rostered = len(pool.rostered_players)
        post_replacement = len(pool.replacement_players)

        if _debug_enabled() and (
            pre_rostered != post_rostered or pre_replacement != post_replacement
        ):
            _debug(
                f"[refill_tiers_after_dedupe] {pos}: "
                f"rostered {pre_rostered}->{post_rostered} "
                f"replacement {pre_replacement}->{post_replacement}"
            )

    return pools


def _debug_enabled() -> bool:
    return os.getenv("MTBL_DEBUG_POOLS") == "1"


def _debug(message: str) -> None:
    if _debug_enabled():
        print(message)
