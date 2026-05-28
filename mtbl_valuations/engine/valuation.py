"""Core valuation functions for TRP system."""

from __future__ import annotations

import math
import statistics
from typing import Any

from ..domain.models import HitterStats, PitcherStats, Player, PositionPool, Role


def is_inverted(category: str) -> bool:
    """Check if a category is inverted (lower is better)."""
    return category in ["ERA", "WHIP"]


def get_categories(role: Role, league_settings: dict[str, Any]) -> list[str]:
    """Get scoring categories for a role."""
    if role == "HITTER":
        return league_settings["batting_categories"]
    elif role == "SP":
        # Replace OUTS with IP for category names
        cats = league_settings["pitching_categories"].copy()
        return ["IP" if c == "OUTS" else c for c in cats if c != "SVHD"]
    else:  # RP
        cats = league_settings["pitching_categories"].copy()
        return ["IP" if c == "OUTS" else c for c in cats if c != "QS"]


def _extract_category_values(
    players: list[Player], category: str, field: str, is_stat: bool
) -> list[float]:
    """Extract values for a specific category from a list of players.

    Args:
        players: List of players to extract values from
        category: Category name to extract (e.g., 'HR', 'R', 'ERA')
        field: Field name to access (e.g., 'normalized_z', 'raw_z')
        is_stat: If True, extract from player.stats; if False, from player.valuation

    Returns:
        List of float values for the category
    """
    values = []
    for player in players:
        obj = player.stats if is_stat else player.valuation

        if isinstance(obj, dict):
            val = obj.get(category, 0.0)
        elif hasattr(obj, field):
            attr = getattr(obj, field, {})
            val = attr.get(category, 0.0) if isinstance(attr, dict) else 0.0
        elif hasattr(obj, category):
            val = getattr(obj, category, 0.0)
        else:
            val = 0.0

        if isinstance(val, (int, float)):
            values.append(float(val))

    return values


def _get_categories(players: list[Player], field: str, is_stat: bool) -> list[str]:
    """Determine categories from first player.

    Args:
        players: List of players
        field: Field name to access
        is_stat: If True, look at player.stats; if False, at player.valuation

    Returns:
        List of category names
    """
    if not players:
        return []

    # Get first player to determine categories
    if (
        not hasattr(players[0], "stats")
        if is_stat
        else not hasattr(players[0], "computed")
    ):
        return []

    sample_obj = players[0].stats if is_stat else players[0].valuation

    # Handle dict-type fields (like raw_z, normalized_z)
    if isinstance(sample_obj, dict):
        return list(sample_obj.keys())
    elif hasattr(sample_obj, field):
        # For nested dict access like stats.category or computed.raw_z
        attr = getattr(sample_obj, field, {})
        if isinstance(attr, dict):
            return list(attr.keys())
        else:
            return []
    else:
        # Direct attribute access
        return [k for k in dir(sample_obj) if not k.startswith("_")]


def calc_means(
    players: list[Player], field: str, is_stat: bool = True
) -> dict[str, float]:
    """Calculate means for all categories."""
    if not players:
        return {}

    categories = _get_categories(players, field, is_stat)
    if not categories:
        return {}

    means = {}
    for cat in categories:
        values = _extract_category_values(players, cat, field, is_stat)
        if values:
            means[cat] = sum(values) / len(values)

    return means


def calc_stdevs(
    players: list[Player], field: str, is_stat: bool = True
) -> dict[str, float]:
    """Calculate standard deviations for all categories."""
    if not players:
        return {}

    means = calc_means(players, field, is_stat)
    if not means:
        return {}

    stdevs = {}
    for cat in means.keys():
        values = _extract_category_values(players, cat, field, is_stat)
        if values and len(values) > 1:
            mean = means[cat]
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            stdevs[cat] = math.sqrt(variance)
        else:
            stdevs[cat] = 0.0

    return stdevs


def get_player_stat(player: Player, category: str) -> float:
    """Get stat value for a player in a category."""
    if not hasattr(player, "stats"):
        return 0.0

    stats = player.stats


    # Map category names to stat fields
    category_map = {
        "IP": "outs",  # Convert IP to outs
        "K/9": "k9",
        "R": "r",
        "HR": "hr",
        "RBI": "rbi",
        "SBN": "sbn",
        "OBP": "obp",
        "SLG": "slg",
        "ERA": "era",
        "WHIP": "whip",
        "QS": "qs",
        "SVHD": "svhd",
    }

    stat_field = category_map.get(category, category.lower())
    value = getattr(stats, stat_field, 0.0)

    # Convert outs to IP for display
    if category == "IP" and stat_field == "outs":
        value = value / 3.0

    return float(value)


def distribute_player_dollars(
    player: Player, pool: PositionPool, store_in_position_valuation: bool = False
) -> dict[str, float]:
    """
    Calculate dollar values per category for a player.

    Path B contract: ``normalized_z`` is already settled (post-shift,
    non-negative-clamped) by the iteration loop, and the SAME formula is
    applied across every tier. This guarantees rostered prices ≥ RLP prices
    for any two players sorted by settled total_z, because:

      - Ranks: rostered = top N by settled total_z
      - Dollars: ``$ = sum_c settled_z[c] * $/Z[c]``
      - Both consume the same metric, so order is preserved.

    Args:
        player: The player to calculate dollars for
        pool: The position pool context
        store_in_position_valuation: If True, stores results in valuations_by_position

    Returns:
        Dictionary of dollar values per category
    """
    # Prefer per-position normalized_z when present, regardless of the
    # store flag. This keeps the read source symmetric with
    # ``calc_pool_dollars_per_z`` so $/Z calibration and per-player dollar
    # distribution agree — otherwise cross-pool players whose top-level
    # ``normalized_z`` was set by another pool's recompute would distribute
    # against a different z than the $/Z calibration used.
    pv = player.valuation.valuations_by_position.get(pool.position)
    if pv is not None and pv.normalized_z:
        normalized_z = pv.normalized_z
    else:
        normalized_z = player.valuation.normalized_z

    dollar_values = {
        category: z_value * pool.dollars_per_z.get(category, 0.0)
        for category, z_value in normalized_z.items()
    }

    if store_in_position_valuation and pool.position in player.valuation.valuations_by_position:
        player.valuation.valuations_by_position[pool.position].dollar_values = dollar_values
        player.valuation.valuations_by_position[pool.position].total_dollars = sum(dollar_values.values())

    return dollar_values


def distribute_pool_dollars(
    pools: dict[str, PositionPool],
    store_per_position: bool = False,
    pin_rlp_to_zero: bool = False,
) -> None:
    """Distribute dollar values to all players across multiple position pools.

    Tier-specific dollar treatment:

    - ROSTERED: ``$ = z·$/Z`` (signed z, the formula). Their dollars sum
      to the pool's category budgets — that's how the league $260×N
      anchoring is preserved.
    - REPLACEMENT: ``$ = z·$/Z`` by default — the formula gives small ±$
      values that read as "what this RLP-tier player is actually worth
      relative to the archetype baseline." When ``pin_rlp_to_zero=True``,
      pinned to $0 instead (RLP-as-freely-available-boundary semantics).
    - BELOW_REPLACEMENT: ``$ = z·$/Z`` (real, almost always negative —
      production below the replacement archetype is honest cost, not
      hidden behind a zero-default). If a below_replacement player's
      formula nets positive (rank-vs-dollar divergence), they're
      promoted to REPLACEMENT (and pinned to $0 in that tier if
      ``pin_rlp_to_zero``; otherwise the formula stands).

    A/B note: ``pin_rlp_to_zero`` defaults to ``False`` (formula on RLP).
    Flip via ``budget_config.json`` to compare $-spread outcomes without
    code change.

    For multi-position players (hitters), per-position values land in
    ``valuations_by_position[pos]`` (when ``store_per_position=True``)
    and the top-level mirror is set only for the player's primary
    position.
    """
    for pos, pool in pools.items():
        # Rostered: real $ from the formula.
        for player in pool.rostered_players:
            dollar_values = distribute_player_dollars(
                player, pool, store_in_position_valuation=store_per_position
            )
            total_dollars = sum(dollar_values.values())
            if player.valuation.primary_position == pos:
                player.valuation.dollar_values = dollar_values
                player.valuation.total_dollars = total_dollars

        # Replacement: formula by default, optionally pinned to $0.
        zero_cats = {c: 0.0 for c in pool.category_budgets.keys()}
        for player in pool.replacement_players:
            if pin_rlp_to_zero:
                dollar_values = dict(zero_cats)
                total_dollars = 0.0
            else:
                dollar_values = distribute_player_dollars(
                    player, pool, store_in_position_valuation=store_per_position
                )
                total_dollars = sum(dollar_values.values())

            if pin_rlp_to_zero and (
                store_per_position
                and pos in player.valuation.valuations_by_position
            ):
                pv = player.valuation.valuations_by_position[pos]
                pv.dollar_values = dict(zero_cats)
                pv.total_dollars = 0.0
            if player.valuation.primary_position == pos:
                player.valuation.dollar_values = dollar_values
                player.valuation.total_dollars = total_dollars

        # Below replacement: earned (mostly negative) $ from the formula.
        # If a below_replacement player's math nets positive, they're a
        # rank-vs-dollar divergence — promote to REPLACEMENT so tier
        # label matches dollar sign.
        promote_to_rlp: list[Player] = []
        for player in pool.below_replacement:
            dollar_values = distribute_player_dollars(
                player, pool, store_in_position_valuation=store_per_position
            )
            total_dollars = sum(dollar_values.values())
            if total_dollars > 0:
                promote_to_rlp.append(player)
                # When pinning RLP to $0, the promoted player must have
                # their $ zeroed to match the tier. With the formula on
                # (default), the formula-$ stands — the promotion just
                # corrects the tier label.
                if pin_rlp_to_zero:
                    if (
                        store_per_position
                        and pos in player.valuation.valuations_by_position
                    ):
                        pv = player.valuation.valuations_by_position[pos]
                        pv.dollar_values = dict(zero_cats)
                        pv.total_dollars = 0.0
                        pv.tier = "REPLACEMENT"
                    if player.valuation.primary_position == pos:
                        player.valuation.dollar_values = dict(zero_cats)
                        player.valuation.total_dollars = 0.0
                        player.valuation.tier = "REPLACEMENT"
                else:
                    if (
                        store_per_position
                        and pos in player.valuation.valuations_by_position
                    ):
                        pv = player.valuation.valuations_by_position[pos]
                        pv.tier = "REPLACEMENT"
                    if player.valuation.primary_position == pos:
                        player.valuation.dollar_values = dollar_values
                        player.valuation.total_dollars = total_dollars
                        player.valuation.tier = "REPLACEMENT"
            else:
                if player.valuation.primary_position == pos:
                    player.valuation.dollar_values = dollar_values
                    player.valuation.total_dollars = total_dollars

        # Move promoted players from below_replacement to replacement_players.
        if promote_to_rlp:
            promoted_ids = {id(p) for p in promote_to_rlp}
            pool.below_replacement = [
                p for p in pool.below_replacement if id(p) not in promoted_ids
            ]
            pool.replacement_players.extend(promote_to_rlp)


_TIER_RANK = {
    "ROSTERED": 3,
    "REPLACEMENT": 2,
    "BELOW_REPLACEMENT": 1,
    "": 0,
}


def resolve_primary_by_best_dollars(
    pools: dict[str, PositionPool],
) -> int:
    """For every multi-pool hitter, pick the best pool as their export
    "headline" and mirror that pool's ``dollar_values`` / ``total_dollars`` /
    ``tier`` / ``normalized_z`` to the top-level ``valuation`` fields.

    Tier hierarchy beats raw $: a player ROSTERED in one pool and
    REPLACEMENT in another stays headlined as ROSTERED even if the RLP
    pool's formula-$ happens to be higher in raw dollars — losing the
    ROSTERED label would (a) break the budget conservation check (rostered
    $ across the league must sum to the league total) and (b) mislabel the
    player's draftable status. Within the same tier, max-$ wins.

    Reason: ``distribute_pool_dollars`` writes top-level only when
    ``primary_position == pool.position``. With
    ``assign_primary_position_from_pool`` blanket-stamping
    ``primary_position=UTIL`` on every UTIL pool member, UTIL-eligible
    multi-pool players (e.g. SS+UTIL) end up showing their UTIL-pool $ in
    the JSON even when their base-pool $ is higher. This pass picks the
    best (tier, $) pool as the export "headline."

    Returns the number of players whose primary_position changed.
    """
    seen: dict[int, Player] = {}
    for pool in pools.values():
        for player in (
            pool.rostered_players
            + pool.replacement_players
            + pool.below_replacement
        ):
            seen.setdefault(id(player), player)

    changes = 0
    for player in seen.values():
        vps = player.valuation.valuations_by_position
        if len(vps) <= 1:
            continue
        best_pos, best_pv = max(
            vps.items(),
            key=lambda kv: (
                _TIER_RANK.get(kv[1].tier, 0),
                kv[1].total_dollars,
            ),
        )
        if player.valuation.primary_position == best_pos:
            continue
        player.valuation.primary_position = best_pos
        player.valuation.tier = best_pv.tier
        player.valuation.dollar_values = dict(best_pv.dollar_values)
        player.valuation.total_dollars = best_pv.total_dollars
        player.valuation.normalized_z = dict(best_pv.normalized_z)
        player.valuation.total_z = (
            best_pv.total_z
            if best_pv.total_z
            else sum(best_pv.normalized_z.values())
        )
        changes += 1
    return changes


def calc_z_scores_for_archetype(
    archetype_stats: dict[str, float],
    reference_players: list[Player],
) -> dict[str, float]:
    """
    Convert archetype raw stats to z-scores against reference population.

    archetype_stats: {'HR': 18.0, 'R': 65.0, ...}  # RAW STATS
    reference_players: Rostered tier players (for mean/stdev)

    Returns: {'HR': -0.5, 'R': -1.2, ...}  # Z-SCORES
    """

    z_scores = {}
    for category, archetype_value in archetype_stats.items():
        values = [get_player_stat(p, category) for p in reference_players]
        if not values:
            z_scores[category] = 0.0
            continue

        mean_val = statistics.mean(values)
        stdev_val = statistics.stdev(values) if len(values) > 1 else 1.0

        # Invert for ERA/WHIP
        if category in ["ERA", "WHIP"]:
            z_scores[category] = (mean_val - archetype_value) / stdev_val
        else:
            z_scores[category] = (archetype_value - mean_val) / stdev_val

    return z_scores


def get_composite_metric(player: Player) -> float:
    """Get composite metric for initial sorting (wRC+ or FIP)."""
    if not hasattr(player, "stats"):
        return 0.0

    stats = player.stats


    if isinstance(stats, HitterStats):
        return stats.wrc_plus
    elif isinstance(stats, PitcherStats):
        # Invert FIP (lower is better, but we want higher sort value)
        return -stats.fip if stats.fip > 0 else 0.0

    return 0.0
