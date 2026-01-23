"""Detailed position-specific CSV exports for analysis."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pandas as pd

from mtbl_valuations.domain.models import (
    HitterPlayer,
    PitcherPlayer,
    PositionPool,
)


def export_hitter_position_csv(
    pool: PositionPool,
    output_path: Path,
    categories: list[str],
) -> None:
    """Export detailed CSV for a single hitter position."""
    rows = []

    # Determine tier based on which list player is in for THIS pool
    player_tiers = {}
    for player in pool.rostered_players:
        player_tiers[player.id] = "ROSTERED"
    for player in pool.replacement_players:
        player_tiers[player.id] = "REPLACEMENT"
    for player in pool.below_replacement:
        player_tiers[player.id] = "BELOW_REPLACEMENT"

    # Export rostered + replacement players from this pool
    # Players may have primary_position != pool.position (e.g., UTIL players in 1B pool)
    all_players = pool.rostered_players + pool.replacement_players

    for player in all_players:
        assert hasattr(player, "stats") and player.stats is not None, (
            "Player missing stats"
        )

        # Get position-specific valuation for THIS pool
        valuation_tier = player_tiers.get(player.id, "UNKNOWN")
        valuation_total_z = player.valuation.total_z
        valuation_normalized_z = player.valuation.normalized_z

        row = {
            # Identity
            "id": player.id,
            "name": player.name,
            "pro_team": player.team,
            "primary_position": player.valuation.primary_position,
            "eligible_positions": "|".join(player.positions),
            "tier": valuation_tier,
            "total_z": round(valuation_total_z, 3),
            "total_dollars": round(player.valuation.total_dollars, 2),
        }

        # Stats for each category: raw stat, z-score, dollars
        hitter_player = cast(HitterPlayer, player)
        hitter_stats = hitter_player.stats
        assert hitter_stats is not None  # type guard
        for cat in categories:
            # Get raw stat value
            if cat == "R":
                raw_val = hitter_stats.r
            elif cat == "HR":
                raw_val = hitter_stats.hr
            elif cat == "RBI":
                raw_val = hitter_stats.rbi
            elif cat == "SBN":
                raw_val = hitter_stats.sbn
            elif cat == "OBP":
                raw_val = hitter_stats.obp
            elif cat == "SLG":
                raw_val = hitter_stats.slg
            else:
                raw_val = 0.0

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
    player_tiers = {}
    for player in pool.rostered_players:
        player_tiers[player.id] = "ROSTERED"
    for player in pool.replacement_players:
        player_tiers[player.id] = "REPLACEMENT"
    for player in pool.below_replacement:
        player_tiers[player.id] = "BELOW_REPLACEMENT"

    # Export rostered + replacement players from this pool
    # Players may have primary_position != pool.position (e.g., UTIL players in 1B pool)
    all_players = pool.rostered_players + pool.replacement_players

    for player in all_players:
        assert hasattr(player, "stats") and player.stats is not None, (
            "Player missing stats"
        )

        row = {
            # Identity
            "id": player.id,
            "name": player.name,
            "pro_team": player.team,
            "primary_position": player.valuation.primary_position,
            "eligible_positions": "|".join(player.positions),
            "tier": player_tiers.get(player.id, "UNKNOWN"),
            "total_z": round(player.valuation.total_z, 3),
            "total_dollars": round(player.valuation.total_dollars, 2),
        }

        # Stats for each category: raw stat, z-score, dollars
        pitcher_player = cast(PitcherPlayer, player)
        pitcher_stats = pitcher_player.stats
        assert pitcher_stats is not None  # type guard
        for cat in categories:
            # Get raw stat value
            if cat == "IP":
                raw_val = pitcher_stats.outs / 3.0  # Convert outs to IP
            elif cat == "ERA":
                raw_val = pitcher_stats.era
            elif cat == "WHIP":
                raw_val = pitcher_stats.whip
            elif cat == "K/9":
                raw_val = pitcher_stats.k9
            elif cat == "QS":
                raw_val = pitcher_stats.qs
            elif cat == "SVHD":
                raw_val = pitcher_stats.svhd
            else:
                raw_val = 0.0

            row[f"{cat}_raw"] = round(raw_val, 3)

            # Skip IP z-score and dollars for RP pools (IP weight is 0.0)
            if pool.position == "RP" and cat == "IP":
                continue

            row[f"{cat}_z"] = round(player.valuation.normalized_z.get(cat, 0.0), 3)
            row[f"{cat}_dollars"] = round(
                player.valuation.dollar_values.get(cat, 0.0), 2
            )

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
