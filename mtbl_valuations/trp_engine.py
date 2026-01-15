"""TRP (True Replacement Price) valuation engine - main pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .budget import (
    allocate_pool_budget,
    allocate_position_budgets,
    calc_dollars_per_z,
    calc_league_budget,
)
from .iteration import iterate_to_convergence
from .loader import load_batters, load_budget_config, load_league_settings, load_pitchers
from .models import Player
from .export_detailed import export_detailed_position_csvs
from .output import (
    validate_budget_balance,
    validate_rlp_z_scores,
    validate_tier_counts,
    write_player_json,
    write_position_summary_csv,
    write_valuations_csv,
)
from .pools import (
    assign_primary_positions,
    build_position_pools,
    build_single_pool,
    build_util_pool,
)
from .valuation import calc_player_dollars


def run_trp_valuation(
    batters_file: Path,
    pitchers_file: Path,
    league_file: Path,
    budget_config_file: Path,
    output_dir: Path,
) -> None:
    """
    Run the complete TRP valuation pipeline.
    This is the 12-phase pipeline from the architecture document.
    """
    print("=== TRP Valuation Engine ===\n")

    # ========================================================================
    # Phase 1: Initialize
    # ========================================================================
    print("Phase 1: Loading data...")
    hitter_players = load_batters(batters_file)
    pitcher_players = load_pitchers(pitchers_file)
    league_settings = load_league_settings(league_file)
    budget_config = load_budget_config(budget_config_file)

    print(f"  Loaded {len(hitter_players)} hitters")
    print(f"  Loaded {len(pitcher_players)} pitchers")
    print(f"  League: {league_settings['num_teams']} teams")

    # Extract players from player objects
    hitters = [hp.player for hp in hitter_players]
    starters = [pp.player for pp in pitcher_players if pp.player.role == "SP"]
    relievers = [pp.player for pp in pitcher_players if pp.player.role == "RP"]

    # ========================================================================
    # Phase 2: Assign primary positions (scarcity-first allocation)
    # ========================================================================
    print("\nPhase 2: Assigning primary positions...")
    hitters = assign_primary_positions(
        hitters,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        "HITTER",
    )

    # Filter out players without primary positions (not assigned to any pool)
    hitters = [h for h in hitters if h.computed.primary_position]

    print(f"  Assigned {len(hitters)} hitters to positions")

    # ========================================================================
    # Phase 3: Split by role
    # ========================================================================
    print("\nPhase 3: Splitting players by role...")

    # Identify pure DH players (only eligible for DH/UTIL)
    pure_dh_players = [
        h
        for h in hitters
        if set(h.positions).issubset({"DH", "UTIL"})
    ]

    # Regular hitters (not pure DH)
    regular_hitters = [
        h for h in hitters if h not in pure_dh_players
    ]

    print(f"  Regular hitters: {len(regular_hitters)}")
    print(f"  Pure DH players: {len(pure_dh_players)}")
    print(f"  Starters: {len(starters)}")
    print(f"  Relievers: {len(relievers)}")

    # ========================================================================
    # Phase 4: Build position pools and iterate to convergence
    # ========================================================================
    print("\nPhase 4: Building hitter pools...")
    hitter_pools = build_position_pools(
        regular_hitters,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        "HITTER",
        budget_config,
    )

    print("  Iterating hitter pools to convergence...")
    hitter_pools = iterate_to_convergence(
        hitter_pools, budget_config, league_settings
    )

    # ========================================================================
    # Phase 5: Build UTIL pool from replacement-tier players + pure DHs
    # ========================================================================
    print("\nPhase 5: Building UTIL pool...")

    # Calculate composite RLP archetype (RAW STATS) from all primary positions
    import statistics
    from .valuation import get_player_stat

    composite_rlp_archetype = {}
    categories = league_settings["batting_categories"]

    for category in categories:
        position_rlp_means = []
        for pool in hitter_pools:  # Primary positions only (no UTIL yet)
            if len(pool.replacement_players) > 0:
                rlp_mean = statistics.mean(
                    get_player_stat(p, category) for p in pool.replacement_players
                )
                position_rlp_means.append(rlp_mean)

        if position_rlp_means:
            composite_rlp_archetype[category] = statistics.mean(position_rlp_means)
        else:
            composite_rlp_archetype[category] = 0.0

    print(f"  Composite RLP archetype: {composite_rlp_archetype}")

    # Build UTIL pool
    util_pool = build_util_pool(
        hitter_pools,
        pure_dh_players,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        budget_config,
    )

    # Iterate with composite RLP baseline
    print("  Iterating UTIL pool with composite RLP baseline...")
    util_pool = iterate_to_convergence(
        [util_pool],
        budget_config,
        league_settings,
        composite_rlp_archetype=composite_rlp_archetype,  # Use composite baseline
    )[0]
    hitter_pools.append(util_pool)

    # ========================================================================
    # Phase 6: Build pitcher pools
    # ========================================================================
    print("\nPhase 6: Building pitcher pools...")
    sp_pool = build_single_pool(
        starters,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        "SP",
        budget_config,
    )
    print("  Iterating SP pool to convergence...")
    sp_pool = iterate_to_convergence([sp_pool], budget_config, league_settings)[0]

    rp_pool = build_single_pool(
        relievers,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        "RP",
        budget_config,
    )
    print("  Iterating RP pool to convergence...")
    rp_pool = iterate_to_convergence([rp_pool], budget_config, league_settings)[0]

    # ========================================================================
    # Phase 7: Calculate league budget structure
    # ========================================================================
    print("\nPhase 7: Calculating league budget...")
    league_budget = calc_league_budget(league_settings, budget_config)

    print(f"  Total budget: ${league_budget.total:,.2f}")
    print(f"  Hitter budget: ${league_budget.hitter_budget:,.2f}")
    print(f"  Pitcher budget: ${league_budget.pitcher_budget:,.2f}")
    print(f"    SP budget: ${league_budget.sp_budget:,.2f}")
    print(f"    RP budget: ${league_budget.rp_budget:,.2f}")

    # ========================================================================
    # Phase 8: Allocate category budgets to positions
    # ========================================================================
    print("\nPhase 8: Allocating category budgets...")
    hitter_pools = allocate_position_budgets(
        hitter_pools, league_budget, budget_config
    )
    sp_pool = allocate_pool_budget(
        sp_pool,
        league_budget.sp_budget,
        budget_config["sp_category_weights"],
    )
    rp_pool = allocate_pool_budget(
        rp_pool,
        league_budget.rp_budget,
        budget_config["rp_category_weights"],
    )

    # ========================================================================
    # Phase 9: Convert Z-scores to dollars
    # ========================================================================
    print("\nPhase 9: Calculating $/Z rates...")
    hitter_pools = calc_dollars_per_z(hitter_pools)
    sp_pool = calc_dollars_per_z([sp_pool])[0]
    rp_pool = calc_dollars_per_z([rp_pool])[0]

    # ========================================================================
    # Phase 10: Value each player
    # ========================================================================
    print("\nPhase 10: Calculating player dollar values...")
    all_pools = hitter_pools + [sp_pool, rp_pool]

    from .models import PositionValuation

    for pool in all_pools:
        for player in pool.rostered_players + pool.replacement_players:
            # Calculate dollar values for THIS position
            dollar_values = calc_player_dollars(player, pool)
            total_dollars = sum(dollar_values.values())

            # Determine tier within THIS pool
            if player in pool.rostered_players:
                tier = "ROSTERED"
            elif player in pool.replacement_players:
                tier = "REPLACEMENT"
            else:
                tier = "BELOW_REPLACEMENT"

            # Store position-specific valuation (don't overwrite!)
            valuation = PositionValuation(
                position=pool.position,
                raw_z=player.computed.raw_z.copy(),
                normalized_z=player.computed.normalized_z.copy(),
                dollar_values=dollar_values,
                total_z=player.computed.total_z,
                total_dollars=total_dollars,
                tier=tier,  # type: ignore
            )
            player.computed.valuations_by_position[pool.position] = valuation

            # Also update the main computed values (for backward compatibility)
            player.computed.dollar_values = dollar_values
            player.computed.total_dollars = total_dollars

    # ========================================================================
    # Phase 11: Validate
    # ========================================================================
    print("\nPhase 11: Validation...")
    validate_budget_balance(all_pools, league_budget)
    validate_tier_counts(
        all_pools, league_settings["roster_slots"], league_settings["num_teams"]
    )
    validate_rlp_z_scores(all_pools)

    # ========================================================================
    # Phase 12: Output
    # ========================================================================
    print("\nPhase 12: Writing output files...")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Write CSV outputs
    write_valuations_csv(
        output_dir / "valuations.csv",
        all_pools,
        {
            "hitter": league_settings["batting_categories"],
            "pitcher": league_settings["pitching_categories"],
        },
    )
    print(f"  ✓ Wrote {output_dir / 'valuations.csv'}")

    write_position_summary_csv(output_dir / "position_summary.csv", all_pools)
    print(f"  ✓ Wrote {output_dir / 'position_summary.csv'}")

    # Write enriched JSON outputs
    with open(batters_file) as f:
        batters_data = json.load(f)

    with open(pitchers_file) as f:
        pitchers_data = json.load(f)

    write_player_json(output_dir / "hitters.json", batters_data, hitter_pools)
    print(f"  ✓ Wrote {output_dir / 'hitters.json'}")

    write_player_json(
        output_dir / "pitchers.json", pitchers_data, [sp_pool, rp_pool]
    )
    print(f"  ✓ Wrote {output_dir / 'pitchers.json'}")

    # Export detailed position-specific CSVs
    export_detailed_position_csvs(
        hitter_pools,
        sp_pool,
        rp_pool,
        output_dir,
        league_settings["batting_categories"],
        league_settings["pitching_categories"],
    )

    print("\n=== TRP Valuation Complete ===")
