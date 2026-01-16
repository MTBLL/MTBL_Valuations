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


def calc_means(
    players: list[Player], field: str, is_stat: bool = True
) -> dict[str, float]:
    """Calculate means for all categories."""
    if not players:
        return {}

    # Get first player to determine categories
    if (
        not hasattr(players[0], "stats")
        if is_stat
        else not hasattr(players[0], "computed")
    ):
        return {}

    sample_obj = players[0].stats if is_stat else players[0].computed  # type: ignore

    # Handle dict-type fields (like raw_z, normalized_z)
    if isinstance(sample_obj, dict):
        categories = list(sample_obj.keys())
    elif hasattr(sample_obj, field):
        # For nested dict access like stats.category or computed.raw_z
        attr = getattr(sample_obj, field, {})
        if isinstance(attr, dict):
            categories = list(attr.keys())
        else:
            return {}
    else:
        # Direct attribute access
        categories = [k for k in dir(sample_obj) if not k.startswith("_")]

    means = {}
    for cat in categories:
        values = []
        for player in players:
            obj = player.stats if is_stat else player.computed  # type: ignore
            if isinstance(obj, dict):
                val = obj.get(cat, 0.0)
            elif hasattr(obj, field):
                attr = getattr(obj, field, {})
                val = attr.get(cat, 0.0) if isinstance(attr, dict) else 0.0
            elif hasattr(obj, cat):
                val = getattr(obj, cat, 0.0)
            else:
                val = 0.0

            if isinstance(val, (int, float)):
                values.append(float(val))

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
        values = []
        for player in players:
            obj = player.stats if is_stat else player.computed  # type: ignore
            if isinstance(obj, dict):
                val = obj.get(cat, 0.0)
            elif hasattr(obj, field):
                attr = getattr(obj, field, {})
                val = attr.get(cat, 0.0) if isinstance(attr, dict) else 0.0
            elif hasattr(obj, cat):
                val = getattr(obj, cat, 0.0)
            else:
                val = 0.0

            if isinstance(val, (int, float)):
                values.append(float(val))

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

    stats = player.stats  # type: ignore

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


def calc_raw_z(
    player: Player, pool: PositionPool, categories: list[str]
) -> dict[str, float]:
    """Calculate raw Z-scores for a player."""
    raw_z = {}

    for category in categories:
        mean = pool.rostered_tier_means.get(category, 0.0)
        stdev = pool.rostered_tier_stdevs.get(category, 1.0)
        value = get_player_stat(player, category)

        if stdev == 0:
            raw_z[category] = 0.0
        elif is_inverted(category):  # ERA, WHIP
            raw_z[category] = (mean - value) / stdev
        else:
            raw_z[category] = (value - mean) / stdev

    return raw_z


def calc_player_dollars(player: Player, pool: PositionPool) -> dict[str, float]:
    """Calculate dollar values per category for a player."""
    dollar_values = {}

    for category, z_value in player.computed.normalized_z.items():
        rate = pool.dollars_per_z.get(category, 0.0)
        dollar_values[category] = z_value * rate

    return dollar_values


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

    stats = player.stats  # type: ignore

    if isinstance(stats, HitterStats):
        return stats.wrc_plus
    elif isinstance(stats, PitcherStats):
        # Invert FIP (lower is better, but we want higher sort value)
        return -stats.fip if stats.fip > 0 else 0.0

    return 0.0
