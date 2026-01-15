"""Detailed position-specific CSV exports for analysis."""

from __future__ import annotations

from pathlib import Path
import pandas as pd

from ..domain.models import PositionPool


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

    all_players = (
        pool.rostered_players + pool.replacement_players + pool.below_replacement
    )

    for player in all_players:
        if not hasattr(player, "stats") or player.stats is None:
            continue

        # Get position-specific valuation for THIS pool
        valuation = player.computed.valuations_by_position.get(pool.position)
        if not valuation:
            # Fallback to main computed values if no position-specific valuation
            valuation_tier = player_tiers.get(player.id, "UNKNOWN")
            valuation_total_z = player.computed.total_z
            valuation_total_dollars = player.computed.total_dollars
            valuation_normalized_z = player.computed.normalized_z
            valuation_dollar_values = player.computed.dollar_values
        else:
            valuation_tier = valuation.tier
            valuation_total_z = valuation.total_z
            valuation_total_dollars = valuation.total_dollars
            valuation_normalized_z = valuation.normalized_z
            valuation_dollar_values = valuation.dollar_values

        row = {
            # Identity
            "id": player.id,
            "name": player.name,
            "pro_team": player.team,
            "primary_position": player.computed.primary_position,
            "eligible_positions": "|".join(player.positions),
            "tier": valuation_tier,
            "total_z": round(valuation_total_z, 3),
            "total_dollars": round(valuation_total_dollars, 2),
        }

        # Stats for each category: raw stat, z-score, dollars
        stats = player.stats  # type: ignore
        for cat in categories:
            # Get raw stat value
            if cat == "R":
                raw_val = stats.r
            elif cat == "HR":
                raw_val = stats.hr
            elif cat == "RBI":
                raw_val = stats.rbi
            elif cat == "SBN":
                raw_val = stats.sbn
            elif cat == "OBP":
                raw_val = stats.obp
            elif cat == "SLG":
                raw_val = stats.slg
            else:
                raw_val = 0.0

            row[f"{cat}_raw"] = round(raw_val, 3)
            row[f"{cat}_z"] = round(valuation_normalized_z.get(cat, 0.0), 3)
            row[f"{cat}_dollars"] = round(valuation_dollar_values.get(cat, 0.0), 2)

        rows.append(row)

    # Sort by total dollars descending
    rows = sorted(rows, key=lambda r: r["total_dollars"], reverse=True)

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

    all_players = (
        pool.rostered_players + pool.replacement_players + pool.below_replacement
    )

    for player in all_players:
        if not hasattr(player, "stats") or player.stats is None:
            continue

        row = {
            # Identity
            "id": player.id,
            "name": player.name,
            "pro_team": player.team,
            "primary_position": player.computed.primary_position,
            "eligible_positions": "|".join(player.positions),
            "tier": player_tiers.get(player.id, "UNKNOWN"),
            "total_z": round(player.computed.total_z, 3),
            "total_dollars": round(player.computed.total_dollars, 2),
        }

        # Stats for each category: raw stat, z-score, dollars
        stats = player.stats  # type: ignore
        for cat in categories:
            # Get raw stat value
            if cat == "IP":
                raw_val = stats.outs / 3.0  # Convert outs to IP
            elif cat == "ERA":
                raw_val = stats.era
            elif cat == "WHIP":
                raw_val = stats.whip
            elif cat == "K/9":
                raw_val = stats.k9
            elif cat == "QS":
                raw_val = stats.qs
            elif cat == "SVHD":
                raw_val = stats.svhd
            else:
                raw_val = 0.0

            row[f"{cat}_raw"] = round(raw_val, 3)
            row[f"{cat}_z"] = round(player.computed.normalized_z.get(cat, 0.0), 3)
            row[f"{cat}_dollars"] = round(
                player.computed.dollar_values.get(cat, 0.0), 2
            )

        rows.append(row)

    # Sort by total dollars descending
    rows = sorted(rows, key=lambda r: r["total_dollars"], reverse=True)

    # Write to CSV
    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(output_path, index=False)


def export_detailed_position_csvs(
    hitter_pools: list[PositionPool],
    sp_pool: PositionPool,
    rp_pool: PositionPool,
    output_dir: Path,
    hitter_categories: list[str],
    pitcher_categories: list[str],
) -> None:
    """Export detailed position-specific CSVs for analysis."""
    print("\n  Exporting detailed position CSVs...")

    # Export each hitter position
    for pool in hitter_pools:
        filename = f"{pool.position.lower()}_detailed.csv"
        export_hitter_position_csv(
            pool, output_dir / filename, hitter_categories
        )
        print(f"    ✓ Wrote {output_dir / filename}")

    # Export SP
    sp_categories = ["IP", "ERA", "WHIP", "K/9", "QS"]
    export_pitcher_pool_csv(sp_pool, output_dir / "sp_detailed.csv", sp_categories)
    print(f"    ✓ Wrote {output_dir / 'sp_detailed.csv'}")

    # Export RP
    rp_categories = ["IP", "ERA", "WHIP", "K/9", "SVHD"]
    export_pitcher_pool_csv(rp_pool, output_dir / "rp_detailed.csv", rp_categories)
    print(f"    ✓ Wrote {output_dir / 'rp_detailed.csv'}")
