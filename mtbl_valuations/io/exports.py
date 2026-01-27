"""Detailed position-specific CSV exports for analysis."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pandas as pd

from mtbl_valuations.domain.models import (
    HitterPlayer,
    PitcherPlayer,
    Player,
    PositionPool,
    Tier,
)

# Category to stat attribute mapping for hitters
_HITTER_STAT_MAP = {
    "R": lambda stats: stats.r,
    "HR": lambda stats: stats.hr,
    "RBI": lambda stats: stats.rbi,
    "SBN": lambda stats: stats.sbn,
    "OBP": lambda stats: stats.obp,
    "SLG": lambda stats: stats.slg,
}

# Category to stat attribute mapping for pitchers
_PITCHER_STAT_MAP = {
    "IP": lambda stats: stats.outs / 3.0,  # Convert outs to IP
    "ERA": lambda stats: stats.era,
    "WHIP": lambda stats: stats.whip,
    "K/9": lambda stats: stats.k9,
    "QS": lambda stats: stats.qs,
    "SVHD": lambda stats: stats.svhd,
}


def _build_tier_map(pool: PositionPool) -> dict[str, Tier]:
    """Build player ID -> tier mapping for a pool.

    Args:
        pool: Position pool containing players in different tiers

    Returns:
        Dictionary mapping player IDs to their tier designation
    """
    tier_map: dict[str, Tier] = {}
    for player in pool.rostered_players:
        tier_map[player.id] = "ROSTERED"
    for player in pool.replacement_players:
        tier_map[player.id] = "REPLACEMENT"
    for player in pool.below_replacement:
        tier_map[player.id] = "BELOW_REPLACEMENT"
    return tier_map


def _get_position_valuation(
    player: Player, position: str
) -> tuple[float, dict[str, float], dict[str, float], float]:
    """
    Get position-specific valuation data.

    Returns: (total_z, normalized_z, dollar_values, total_dollars)
    """
    if position in player.valuation.valuations_by_position:
        pos_val = player.valuation.valuations_by_position[position]
        return (
            pos_val.total_z,
            pos_val.normalized_z,
            pos_val.dollar_values,
            pos_val.total_dollars,
        )
    else:
        # Fallback to top-level for single-position players
        return (
            player.valuation.total_z,
            player.valuation.normalized_z,
            player.valuation.dollar_values,
            player.valuation.total_dollars,
        )


def export_hitter_position_csv(
    pool: PositionPool,
    output_path: Path,
    categories: list[str],
) -> None:
    """Export detailed CSV for a single hitter position."""
    rows = []

    # Determine tier based on which list player is in for THIS pool
    player_tiers = _build_tier_map(pool)

    # Export rostered + replacement players from this pool
    # Players may have primary_position != pool.position (e.g., UTIL players in 1B pool)
    all_players = pool.rostered_players + pool.replacement_players

    for player in all_players:
        assert hasattr(player, "stats") and player.stats is not None, (
            "Player missing stats"
        )

        # Get position-specific valuation for THIS pool
        valuation_tier = player_tiers.get(player.id, "UNKNOWN")
        (
            valuation_total_z,
            valuation_normalized_z,
            valuation_dollar_values,
            valuation_total_dollars,
        ) = _get_position_valuation(player, pool.position)

        row = {
            # Identity
            "id": player.id,
            "name": player.name,
            "pro_team": player.team,
            "primary_position": player.valuation.primary_position,
            "eligible_positions": "|".join(player.positions),
            "tier": valuation_tier,
            "total_z": round(valuation_total_z, 3),
            "total_dollars": round(valuation_total_dollars, 2),
        }

        # Stats for each category: raw stat, z-score, dollars
        hitter_player = cast(HitterPlayer, player)
        hitter_stats = hitter_player.stats
        assert hitter_stats is not None  # type guard
        for cat in categories:
            # Get raw stat value using mapping
            stat_getter = _HITTER_STAT_MAP.get(cat)
            raw_val = stat_getter(hitter_stats) if stat_getter else 0.0

            row[f"{cat}_raw"] = round(raw_val, 3)
            row[f"{cat}_z"] = round(valuation_normalized_z.get(cat, 0.0), 3)

        rows.append(row)

    # Sort by total_z descending (reflects actual performance tier)
    rows = sorted(rows, key=lambda r: float(r["total_z"]), reverse=True)  # type: ignore[arg-type]

    # Write to CSV
    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(output_path, index=False)


def export_pitcher_pool_csv(
    pool: PositionPool, output_path: Path, categories: list[str]
) -> None:
    """Export detailed CSV for SP or RP pool."""
    rows = []

    # Determine tier based on which list player is in for THIS pool
    player_tiers = _build_tier_map(pool)

    # Export rostered + replacement players from this pool
    # Players may have primary_position != pool.position (e.g., UTIL players in 1B pool)
    all_players = pool.rostered_players + pool.replacement_players

    for player in all_players:
        assert hasattr(player, "stats") and player.stats is not None, (
            "Player missing stats"
        )

        # Get position-specific valuation for THIS pool
        (
            valuation_total_z,
            valuation_normalized_z,
            valuation_dollar_values,
            valuation_total_dollars,
        ) = _get_position_valuation(player, pool.position)

        row = {
            # Identity
            "id": player.id,
            "name": player.name,
            "pro_team": player.team,
            "primary_position": player.valuation.primary_position,
            "eligible_positions": "|".join(player.positions),
            "tier": player_tiers.get(player.id, "UNKNOWN"),
            "total_z": round(valuation_total_z, 3),
            "total_dollars": round(valuation_total_dollars, 2),
        }

        # Stats for each category: raw stat, z-score, dollars
        pitcher_player = cast(PitcherPlayer, player)
        pitcher_stats = pitcher_player.stats
        assert pitcher_stats is not None  # type guard
        for cat in categories:
            # Get raw stat value using mapping
            stat_getter = _PITCHER_STAT_MAP.get(cat)
            raw_val = stat_getter(pitcher_stats) if stat_getter else 0.0

            row[f"{cat}_raw"] = round(raw_val, 3)

            # Skip IP z-score and dollars for RP pools (IP weight is 0.0)
            if pool.position == "RP" and cat == "IP":
                continue

            row[f"{cat}_z"] = round(valuation_normalized_z.get(cat, 0.0), 3)
            row[f"{cat}_dollars"] = round(valuation_dollar_values.get(cat, 0.0), 2)

        rows.append(row)

    # Sort by total_z descending (reflects actual performance tier)
    rows = sorted(rows, key=lambda r: float(r["total_z"]), reverse=True)  # type: ignore[arg-type]

    # Write to CSV
    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(output_path, index=False)


def export_detailed_position_csvs(
    hitter_pools: dict[str, PositionPool],
    sp_pool: PositionPool,
    rp_pool: PositionPool,
    output_dir: Path,
    hitter_categories: list[str],
    pitcher_categories: list[str],
) -> None:
    """Export detailed position-specific CSVs for analysis."""
    print("\n  Exporting detailed position CSVs...")

    # Export each hitter position
    for pos, pool in hitter_pools.items():
        filename = f"{pos.lower()}_detailed.csv"
        export_hitter_position_csv(pool, output_dir / filename, hitter_categories)
        print(f"    ✓ Wrote {output_dir / filename}")

    # Export SP
    sp_categories = ["IP", "ERA", "WHIP", "K/9", "QS"]
    export_pitcher_pool_csv(sp_pool, output_dir / "sp_detailed.csv", sp_categories)
    print(f"    ✓ Wrote {output_dir / 'sp_detailed.csv'}")

    # Export RP
    rp_categories = ["IP", "ERA", "WHIP", "K/9", "SVHD"]
    export_pitcher_pool_csv(rp_pool, output_dir / "rp_detailed.csv", rp_categories)
    print(f"    ✓ Wrote {output_dir / 'rp_detailed.csv'}")
