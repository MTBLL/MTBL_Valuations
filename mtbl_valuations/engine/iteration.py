"""Iteration to convergence logic for TRP system."""

from __future__ import annotations

import hashlib
import math
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
    histories = _init_pool_histories(pools)

    for iteration in range(1, max_iterations + 1):
        changes = 0

        for pos, pool in pools.items():
            # Pool already settled (naturally converged or oscillation-frozen) —
            # don't touch it again this run; further iterations would re-trigger
            # the same flip-flop.
            if histories[pos]["frozen"]:
                continue
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

            # Step 3a: compute RAW above-replacement z per player (in-memory)
            raw_z: dict[str, dict[str, float]] = {}
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
                raw_z[player.id] = z_by_cat

            # Step 3b: per-cat z_baseline_shift = -min(raw z) over CURRENT
            # rostered tier. The shift lets every rostered player's per-cat
            # contribution land at >=0 so dollars are non-negative.
            pool.z_baseline_shift = {}
            for cat in categories:
                rost_raw = [
                    raw_z[p.id][cat]
                    for p in pool.rostered_players
                    if p.id in raw_z
                ]
                min_z = min(rost_raw) if rost_raw else 0.0
                pool.z_baseline_shift[cat] = max(0.0, -min_z)

            # Step 3c: store SETTLED z. total_z is the *sum* of settled
            # per-cat z's — the intuitive "production above replacement"
            # number that shows up in logs / JSON / CSVs.
            for player in all_pool_players:
                rz = raw_z[player.id]
                settled: dict[str, float] = {
                    cat: max(0.0, rz[cat] + pool.z_baseline_shift[cat])
                    for cat in categories
                }
                player.valuation.normalized_z = settled
                player.valuation.total_z = sum(settled.values())

            # Step 3d: ``total_pool_z`` from current rostered + per-cat
            # ``$/Z`` proxy that includes cross-pool production share.
            pool.total_pool_z = {
                c: sum(p.valuation.normalized_z.get(c, 0.0) for p in pool.rostered_players)
                for c in categories
            }
            dollars_per_z_proxy = _compute_dollars_per_z_proxy(
                pool, pools, budget_config
            )

            # Step 5: Re-rank by the per-pool dollar proxy (not total_z) —
            # ``sum_c settled_z[c] * $/Z_proxy[c]`` is proportional to the
            # actual Phase 5 dollar value within this pool, so tier
            # assignment tracks dollar ordering. total_z stays the
            # intuitive sum for downstream display.
            def _rank_global(p: Player) -> float:
                z = p.valuation.normalized_z
                return sum(
                    z.get(c, 0.0) * dollars_per_z_proxy.get(c, 0.0)
                    for c in categories
                )

            all_pool_players = sorted(all_pool_players, key=_rank_global, reverse=True)

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

            # Track best-z snapshot + oscillation status for this pool.
            _observe_iter(pool, histories[pos], iteration, per_position=False)

        # Check convergence
        if changes <= convergence_threshold:
            print(f"Converged after {iteration} iterations")
            converged = True
            break
    else:
        print(f"Max iterations ({max_iterations}) reached")

    # Settle oscillating / max-iter pools on their highest-rostered-z snapshot.
    _settle_pools(pools, histories, converged, per_position=False)

    iter_log = current_logger()
    if iter_log is not None:
        for pos in pools:
            h = histories[pos]
            iter_log.log_converged(
                current_phase(),
                pos,
                iteration,
                converged,
                max_iterations,
                oscillating=h["oscillating"],
                best_iter=h["best_iter"],
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
    histories = _init_pool_histories(pools)

    for iteration in range(1, max_iterations + 1):
        changes = 0

        for pos, pool in pools.items():
            if histories[pos]["frozen"]:
                continue
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

            # Step 3a: compute RAW above-replacement z per player (in-memory)
            raw_z: dict[str, dict[str, float]] = {}
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
                raw_z[player.id] = z_by_cat

            # Step 3b: per-cat z_baseline_shift from current rostered raw z.
            pool.z_baseline_shift = {}
            for cat in categories:
                rost_raw = [
                    raw_z[p.id][cat]
                    for p in pool.rostered_players
                    if p.id in raw_z
                ]
                min_z = min(rost_raw) if rost_raw else 0.0
                pool.z_baseline_shift[cat] = max(0.0, -min_z)

            # Step 3c: store SETTLED z. total_z is the *sum* of settled
            # per-cat z's (intuitive). Rank uses a separate inline proxy.
            for player in all_pool_players:
                rz = raw_z[player.id]
                settled: dict[str, float] = {
                    cat: max(0.0, rz[cat] + pool.z_baseline_shift[cat])
                    for cat in categories
                }
                _ensure_position_valuation(player, pos)
                pv = player.valuation.valuations_by_position[pos]
                pv.normalized_z = settled
                pv.total_z = sum(settled.values())

            # Step 3d: per-pool dollar proxy — same approach as the global
            # path but reading per-position normalized_z.
            pool.total_pool_z = {
                c: sum(
                    p.valuation.valuations_by_position[pos].normalized_z.get(c, 0.0)
                    for p in pool.rostered_players
                    if pos in p.valuation.valuations_by_position
                )
                for c in categories
            }
            dollars_per_z_proxy = _compute_dollars_per_z_proxy(
                pool, pools, budget_config
            )

            # Step 5: Re-rank by per-pool dollar proxy (inline) so tier
            # assignment tracks dollar ordering. total_z (= sum) is for
            # display only.
            def _rank_per_position(p: Player) -> float:
                pvz = p.valuation.valuations_by_position[pos].normalized_z
                return sum(
                    pvz.get(c, 0.0) * dollars_per_z_proxy.get(c, 0.0)
                    for c in categories
                )

            all_pool_players = sorted(
                all_pool_players, key=_rank_per_position, reverse=True
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

            # Track best-z snapshot + oscillation status for this pool.
            _observe_iter(pool, histories[pos], iteration, per_position=True)

        # Check convergence
        if changes <= convergence_threshold:
            print(f"Converged after {iteration} iterations")
            converged = True
            break
    else:
        print(f"Max iterations ({max_iterations}) reached")

    _settle_pools(pools, histories, converged, per_position=True)

    iter_log = current_logger()
    if iter_log is not None:
        for pos in pools:
            h = histories[pos]
            iter_log.log_converged(
                current_phase(),
                pos,
                iteration,
                converged,
                max_iterations,
                oscillating=h["oscillating"],
                best_iter=h["best_iter"],
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


def _compute_dollars_per_z_proxy(
    pool: PositionPool,
    pools: dict[str, PositionPool],
    budget_config: dict[str, Any],
) -> dict[str, float]:
    """Per-cat ``$/Z`` proxy used as the in-iter rank metric.

    True dollar value = ``sum_c settled_z[c] * pool_budget[c] / total_pool_z[c]``
    where ``pool_budget[c] = cat_weight[c] * pool_production_share[c]``
    (modulo a per-pool constant that doesn't affect ranking).

    ``pool_production_share[c]`` is per-category and depends on cross-pool
    state, so it can only be computed when the full hitter-pools dict is
    available. For UTIL's Phase 4b iter (single-pool dict), the share
    collapses to 1.0 and the proxy reduces to ``cat_weight / total_pool_z`` —
    the Path B weighted total in disguise. That's acceptable: UTIL gets
    re-allocated against the full hitter pools at Phase 5.
    """
    cat_weights = _cat_weights_for(pool.role, budget_config)
    if pool.role != "HITTER" or len(pools) <= 1:
        eps = 1e-9
        return {
            c: cat_weights.get(c, 0.0) / max(pool.total_pool_z.get(c, 0.0), eps)
            for c in cat_weights
        }

    counting = ["R", "HR", "RBI", "SBN"]
    rate = ["OBP", "SLG"]
    pa_weights = budget_config.get("pa_weights") or {}
    default_pa = float(pa_weights.get("default", 600))

    # Counting stats: production share by raw rostered totals.
    total_raw: dict[str, float] = {
        c: sum(
            sum(get_player_stat(p, c) for p in op.rostered_players)
            for op in pools.values()
        )
        for c in counting
    }
    pool_raw = {
        c: sum(get_player_stat(p, c) for p in pool.rostered_players) for c in counting
    }
    share: dict[str, float] = {
        c: (pool_raw[c] / total_raw[c]) if total_raw[c] > 0 else 0.0
        for c in counting
    }

    # Rate stats: PA-weighted rostered mean / cross-pool total.
    total_w_obp = 0.0
    total_w_slg = 0.0
    pool_w_obp = 0.0
    pool_w_slg = 0.0
    for opos, op in pools.items():
        if not op.rostered_players:
            continue
        pa_w = float(pa_weights.get(opos, default_pa))
        op_obp = sum(get_player_stat(p, "OBP") for p in op.rostered_players) / len(
            op.rostered_players
        )
        op_slg = sum(get_player_stat(p, "SLG") for p in op.rostered_players) / len(
            op.rostered_players
        )
        total_w_obp += op_obp * pa_w
        total_w_slg += op_slg * pa_w
        if op is pool:
            pool_w_obp = op_obp * pa_w
            pool_w_slg = op_slg * pa_w
    share["OBP"] = pool_w_obp / total_w_obp if total_w_obp > 0 else 0.0
    share["SLG"] = pool_w_slg / total_w_slg if total_w_slg > 0 else 0.0

    # $/Z proxy = cat_weight * share / total_pool_z. The league_cat_budget
    # factor cancels out for ranking purposes.
    eps = 1e-9
    return {
        c: cat_weights.get(c, 0.0) * share.get(c, 0.0)
        / max(pool.total_pool_z.get(c, 0.0), eps)
        for c in counting + rate
    }


def _cat_weights_for(role: str, budget_config: dict[str, Any]) -> dict[str, float]:
    """Return the per-category weights from budget_config for this pool's
    role. Used as a static proxy for $/Z in the rank metric so tier
    assignment tracks the dollar formula.

    Pitchers split into SP / RP because their category weight tables differ
    (e.g., SP weights QS, RP weights SVHD).
    """
    if role == "HITTER":
        weights = budget_config.get("hitter_category_weights") or {}
    elif role == "SP":
        weights = budget_config.get("sp_category_weights") or {}
    elif role == "RP":
        weights = budget_config.get("rp_category_weights") or {}
    else:
        weights = {}
    return {k: float(v) for k, v in weights.items()}


def _safe_mean(nums: Iterable[float]) -> float:
    nums = list(nums)
    return statistics.mean(nums) if nums else 0.0


def _safe_stdev(nums: Iterable[float]) -> float:
    nums = list(nums)
    # statistics.stdev requires at least 2 points; decide what you want otherwise
    return statistics.stdev(nums) if len(nums) >= 2 else 0.0


### ===  oscillation handling / best-snapshot restore  === ###


def _composition_hash(pool: PositionPool) -> str:
    """Stable, order-independent hash of the rostered-tier player IDs."""
    return hashlib.sha1(
        ",".join(sorted(p.id for p in pool.rostered_players)).encode()
    ).hexdigest()[:10]


def _rostered_z_sum(pool: PositionPool, per_position: bool) -> float:
    """Sum of total_z across the rostered tier. This is the figure we
    maximize when settling an oscillating pool on its best snapshot."""
    pos = pool.position
    if per_position:
        return sum(
            p.valuation.valuations_by_position[pos].total_z
            for p in pool.rostered_players
            if pos in p.valuation.valuations_by_position
        )
    return sum(p.valuation.total_z for p in pool.rostered_players)


def _capture_pool_snapshot(
    pool: PositionPool, per_position: bool
) -> dict[str, Any]:
    """Capture enough state to fully restore a pool to this iteration later.

    Captures total_z explicitly so restoration preserves the iter's rank
    metric — under Path B total_z is the weighted settled sum, not the
    naive sum of normalized_z values.
    """
    pos = pool.position
    z_by_player: dict[str, dict[str, float]] = {}
    total_z_by_player: dict[str, float] = {}
    for p in (
        pool.rostered_players + pool.replacement_players + pool.below_replacement
    ):
        if per_position and pos in p.valuation.valuations_by_position:
            pv = p.valuation.valuations_by_position[pos]
            z_by_player[p.id] = dict(pv.normalized_z)
            total_z_by_player[p.id] = pv.total_z
        else:
            z_by_player[p.id] = dict(p.valuation.normalized_z)
            total_z_by_player[p.id] = p.valuation.total_z
    return {
        "rostered": list(pool.rostered_players),
        "replacement": list(pool.replacement_players),
        "below": list(pool.below_replacement),
        "stdevs": dict(pool.rostered_tier_stdevs),
        "rlp_raw_avg": dict(pool.rlp_raw_avg),
        "z_baseline_shift": dict(pool.z_baseline_shift),
        "z_by_player": z_by_player,
        "total_z_by_player": total_z_by_player,
    }


def _restore_pool_snapshot(
    pool: PositionPool, snapshot: dict[str, Any], per_position: bool
) -> None:
    """Restore a pool (tier composition + z-scores + scale) from a snapshot."""
    pool.rostered_players = list(snapshot["rostered"])
    pool.replacement_players = list(snapshot["replacement"])
    pool.below_replacement = list(snapshot["below"])
    pool.rostered_tier_stdevs = dict(snapshot["stdevs"])
    pool.rlp_raw_avg = dict(snapshot["rlp_raw_avg"])
    pool.z_baseline_shift = dict(snapshot.get("z_baseline_shift") or {})
    pos = pool.position
    total_z_by_player = snapshot.get("total_z_by_player") or {}
    for p in (
        pool.rostered_players + pool.replacement_players + pool.below_replacement
    ):
        z = snapshot["z_by_player"].get(p.id)
        if z is None:
            continue
        # Restore the exact total_z that was in effect at snapshot time
        # (the weighted settled sum under Path B; falls back to a plain sum
        # for older snapshots without the field).
        captured_total = total_z_by_player.get(p.id, sum(z.values()))
        if per_position:
            _ensure_position_valuation(p, pos)
            pv = p.valuation.valuations_by_position[pos]
            pv.normalized_z = dict(z)
            pv.total_z = captured_total
        else:
            p.valuation.normalized_z = dict(z)
            p.valuation.total_z = captured_total
    if per_position:
        assign_player_tiers_per_position(pool)
    else:
        assign_player_tiers_global(pool)


def _init_pool_histories(
    pools: dict[str, PositionPool],
) -> dict[str, dict[str, Any]]:
    """Per-pool tracking state used by the iterate-to-convergence loops."""
    return {
        pos: {
            "best_z_sum": -math.inf,
            "best_snapshot": None,
            "best_iter": 0,
            "recent_hashes": [],
            "frozen": False,
            "naturally_converged": False,
            "oscillating": False,
        }
        for pos in pools
    }


def _observe_iter(
    pool: PositionPool,
    history: dict[str, Any],
    iteration: int,
    per_position: bool,
) -> None:
    """Update per-pool history at the end of one iteration.

    Updates the best-Z snapshot if this iteration improved on the prior best,
    then classifies the pool's status by comparing this iteration's
    composition hash to recent history:

      - Same hash as the immediately previous iter -> naturally converged
        (no movement); pool is frozen but no restoration will be needed.
      - Same hash as an older iter (period >= 2) -> oscillating; pool is
        frozen and will be restored to its best snapshot post-loop.
    """
    z_sum = _rostered_z_sum(pool, per_position)
    if z_sum > history["best_z_sum"]:
        history["best_z_sum"] = z_sum
        history["best_iter"] = iteration
        history["best_snapshot"] = _capture_pool_snapshot(pool, per_position)
    h = _composition_hash(pool)
    recent: list[str] = history["recent_hashes"]
    if recent and h == recent[-1]:
        history["naturally_converged"] = True
        history["frozen"] = True
    elif h in recent:
        history["oscillating"] = True
        history["frozen"] = True
    recent.append(h)
    if len(recent) > 4:
        recent.pop(0)


def recompute_pool_z_in_place(
    pool: PositionPool,
    pools: dict[str, PositionPool],
    budget_config: dict[str, Any],
    league_settings: dict[str, Any],
    per_position: bool,
) -> None:
    """Refresh a pool's derived state — rostered-tier stdev, replacement
    raw mean, per-cat baseline shift, per-player settled z, total_pool_z,
    and the dollar-proxy ``total_z`` rank metric — for the pool's CURRENT
    tier composition.

    Does NOT re-rank or swap players. Used by the Phase 5 swap-pass after
    a manual rostered <-> RLP swap so dollars can be re-computed against
    the new tier shape without the iteration loop trying to undo the swap.
    """
    categories = get_categories(pool.role, league_settings)
    pos = pool.position

    rostered = [p for p in pool.rostered_players if hasattr(p, "stats")]
    rlp_tier = [p for p in pool.replacement_players if hasattr(p, "stats")]
    below = [p for p in pool.below_replacement if hasattr(p, "stats")]
    all_pool_players = rostered + rlp_tier + below

    pool.rostered_tier_stdevs = {
        c: _safe_stdev([get_player_stat(p, c) for p in rostered]) for c in categories
    }
    pool.rlp_raw_avg = {
        c: _safe_mean(get_player_stat(p, c) for p in rlp_tier) for c in categories
    }

    raw_z: dict[str, dict[str, float]] = {}
    for p in all_pool_players:
        zd: dict[str, float] = {}
        for c in categories:
            x = get_player_stat(p, c)
            mu = pool.rlp_raw_avg.get(c, 0.0)
            sd = pool.rostered_tier_stdevs.get(c, 0.0)
            delta = (x - mu) if c not in ("ERA", "WHIP") else (mu - x)
            zd[c] = (delta / sd) if sd else 0.0
        raw_z[p.id] = zd
    pool.z_baseline_shift = {}
    for c in categories:
        rost_raw = [raw_z[p.id][c] for p in pool.rostered_players if p.id in raw_z]
        min_z = min(rost_raw) if rost_raw else 0.0
        pool.z_baseline_shift[c] = max(0.0, -min_z)

    for p in all_pool_players:
        settled = {
            c: max(0.0, raw_z[p.id][c] + pool.z_baseline_shift[c])
            for c in categories
        }
        if per_position:
            _ensure_position_valuation(p, pos)
            pv = p.valuation.valuations_by_position[pos]
            pv.normalized_z = settled
        # Always also mirror to top-level: ``calc_pool_dollars_per_z``
        # reads ``player.valuation.normalized_z`` regardless of per_position
        # mode, so per_position swap-pass refreshes must keep top-level in
        # sync to preserve budget conservation across pools.
        p.valuation.normalized_z = settled

    if per_position:
        pool.total_pool_z = {
            c: sum(
                p.valuation.valuations_by_position[pos].normalized_z.get(c, 0.0)
                for p in pool.rostered_players
                if pos in p.valuation.valuations_by_position
            )
            for c in categories
        }
    else:
        pool.total_pool_z = {
            c: sum(p.valuation.normalized_z.get(c, 0.0) for p in pool.rostered_players)
            for c in categories
        }

    # total_z is the intuitive sum of settled per-cat z's. The dollar-
    # proxy rank metric is used inline during the iter loop sort; this
    # recompute step doesn't re-rank (tier composition is locked by the
    # swap-pass), so we just store the sum here for display.
    for p in all_pool_players:
        if per_position:
            pv = p.valuation.valuations_by_position[pos]
            pv.total_z = sum(pv.normalized_z.values())
        # Mirror to top-level so downstream consumers (writers,
        # build_player_valuations, validators) see consistent values.
        p.valuation.total_z = sum(p.valuation.normalized_z.values())

    if per_position:
        assign_player_tiers_per_position(pool)
        # Mirror tier flag to top-level too (writers / downstream read
        # player.valuation.tier; without this mirror after a swap the
        # top-level tier stays stale at Phase 3d's value).
        for p in pool.rostered_players:
            p.valuation.tier = "ROSTERED"
        for p in pool.replacement_players:
            p.valuation.tier = "REPLACEMENT"
        for p in pool.below_replacement:
            p.valuation.tier = "BELOW_REPLACEMENT"
    else:
        assign_player_tiers_global(pool)


def _settle_pools(
    pools: dict[str, PositionPool],
    histories: dict[str, dict[str, Any]],
    converged: bool,
    per_position: bool,
) -> None:
    """After the iteration loop exits, restore each pool to its best
    snapshot when the pool oscillated or the global loop never reached
    natural convergence (max-iter exit)."""
    for pos, pool in pools.items():
        h = histories[pos]
        if h["best_snapshot"] is None:
            continue
        if h["oscillating"] or (not converged and not h["naturally_converged"]):
            _restore_pool_snapshot(pool, h["best_snapshot"], per_position)
