"""Output generation for TRP valuation system."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ..domain.models import PositionPool


def write_valuations_csv(
    output_path: Path,
    all_pools: list[PositionPool],
    categories: dict[str, list[str]],
) -> None:
    """Write player valuations to CSV."""
    rows = []

    for pool in all_pools:
        all_players = (
            pool.rostered_players
            + pool.replacement_players
            + pool.below_replacement
        )

        for player in all_players:
            row = {
                "player_id": player.id,
                "name": player.name,
                "position": pool.position,
                "role": pool.role,
                "total_z": round(player.computed.total_z, 3),
                "dollar_value": round(player.computed.total_dollars, 2),
                "tier": player.computed.tier,
            }

            # Add Z-scores per category
            for cat in player.computed.normalized_z.keys():
                row[f"z_{cat}"] = round(player.computed.normalized_z[cat], 3)

            # Add dollar values per category
            for cat in player.computed.dollar_values.keys():
                row[f"dollar_{cat}"] = round(player.computed.dollar_values[cat], 2)

            rows.append(row)

    # Sort by dollar value descending
    rows = sorted(rows, key=lambda r: r["dollar_value"], reverse=True)

    # Write to CSV
    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(output_path, index=False)


def write_position_summary_csv(
    output_path: Path, all_pools: list[PositionPool]
) -> None:
    """Write position summary to CSV."""
    rows = []

    for pool in all_pools:
        row = {
            "position": pool.position,
            "role": pool.role,
            "rostered_count": len(pool.rostered_players),
            "replacement_tier_count": len(pool.replacement_players),
            "total_budget": sum(pool.category_budgets.values()),
        }

        # Add $/Z per category
        for cat, rate in pool.dollars_per_z.items():
            row[f"dollars_per_z_{cat}"] = round(rate, 3)

        # Add replacement baseline stats
        for cat, value in pool.rlp_raw_z_avg.items():
            row[f"replacement_baseline_{cat}"] = round(value, 3)

        rows.append(row)

    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(output_path, index=False)


def write_player_json(
    output_path: Path,
    input_data: list[dict[str, Any]],
    all_pools: list[PositionPool],
) -> None:
    """
    Write enriched player JSON with valuation data.
    Matches input schema and appends stats.valuations object.
    """
    # Build lookup of player valuations
    player_valuations: dict[str, dict[str, Any]] = {}

    for pool in all_pools:
        all_players = (
            pool.rostered_players
            + pool.replacement_players
            + pool.below_replacement
        )

        for player in all_players:
            player_valuations[player.id] = {
                "primary_position": player.computed.primary_position,
                "tier": player.computed.tier,
                "total_z": round(player.computed.total_z, 3),
                "total_dollars": round(player.computed.total_dollars, 2),
                "z_scores": {
                    cat: round(val, 3)
                    for cat, val in player.computed.normalized_z.items()
                },
                "dollar_values": {
                    cat: round(val, 2)
                    for cat, val in player.computed.dollar_values.items()
                },
            }

    # Enrich input data with valuations
    enriched = []
    for record in input_data:
        player_id = str(record["id_espn"])
        if player_id in player_valuations:
            if "stats" not in record:
                record["stats"] = {}
            record["stats"]["valuations"] = player_valuations[player_id]
        enriched.append(record)

    # Write to JSON
    with open(output_path, "w") as f:
        json.dump(enriched, f, indent=2)
