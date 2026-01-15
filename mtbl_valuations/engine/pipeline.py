"""TRP (True Replacement Price) valuation engine - main pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from ..io.exports import export_detailed_position_csvs
from ..io.loader import (
    load_batters,
    load_budget_config,
    load_league_settings,
    load_pitchers,
)
from ..io.writers import (
    write_player_json,
    write_position_summary_csv,
    write_valuations_csv,
)
from ..validation.checks import (
    validate_budget_balance,
    validate_rlp_z_scores,
    validate_tier_counts,
)
from .budget import (
    allocate_pool_budget,
    allocate_position_budgets,
    calc_dollars_per_z,
    calc_league_budget,
)
from .iteration import iterate_to_convergence, stabilize_position_assignments
from .pools import (
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
    # Phase 2: Split by role
    # ========================================================================
    print("\nPhase 2: Splitting players by role...")

    # ========================================================================
    # Phase 3: Split by role
    # ========================================================================
    print("\nPhase 3: Splitting players by role...")

    # Identify pure DH players (only eligible for DH/UTIL, no pitcher eligibility)
    # Exclude players like Shohei Ohtani who have DH/UTIL but also pitcher positions
    pitcher_positions = {"SP", "RP", "P"}
    pure_dh_players = [
        h
        for h in hitters
        if set(h.positions).issubset({"DH", "UTIL"})
        and not any(pos in pitcher_positions for pos in h.positions)
    ]

    # Regular hitters (not pure DH)
    regular_hitters = [h for h in hitters if h not in pure_dh_players]

    print(f"  Regular hitters: {len(regular_hitters)}")
    print(f"  Pure DH players: {len(pure_dh_players)}")
    print(f"  Starters: {len(starters)}")
    print(f"  Relievers: {len(relievers)}")

    # ========================================================================
    # Phase 3: Build position pools and iterate to convergence (multi-eligible)
    # ========================================================================
    print("\nPhase 3: Building hitter pools (multi-eligible)...")
    hitter_pools = build_position_pools(
        regular_hitters,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        "HITTER",
        budget_config,
        use_eligibility=True,  # Players appear in ALL eligible positions
    )

    print("  Iterating hitter pools to convergence (per-pool tracking)...")
    hitter_pools = iterate_to_convergence(
        hitter_pools,
        budget_config,
        league_settings,
        track_per_pool=True,  # Store Z-scores per position
    )

    # ========================================================================
    # Phase 4: UTIL pool will be built after position stability (Phase 10.5)
    # ========================================================================
    print("\nPhase 4: Deferring UTIL pool build until after position stability...")
    print(f"  Pure DH players: {len(pure_dh_players)} (will be added to UTIL later)")

    # ========================================================================
    # Phase 5: Build pitcher pools
    # ========================================================================
    print("\nPhase 5: Building pitcher pools...")
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
    # Phase 6: Calculate league budget structure
    # ========================================================================
    print("\nPhase 6: Calculating league budget...")
    league_budget = calc_league_budget(league_settings, budget_config)

    print(f"  Total budget: ${league_budget.total:,.2f}")
    print(f"  Hitter budget: ${league_budget.hitter_budget:,.2f}")
    print(f"  Pitcher budget: ${league_budget.pitcher_budget:,.2f}")
    print(f"    SP budget: ${league_budget.sp_budget:,.2f}")
    print(f"    RP budget: ${league_budget.rp_budget:,.2f}")

    # ========================================================================
    # Phase 7: Allocate category budgets to positions
    # ========================================================================
    print("\nPhase 7: Allocating category budgets...")
    hitter_pools = allocate_position_budgets(hitter_pools, league_budget, budget_config)
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
    # Phase 8: Convert Z-scores to dollars
    # ========================================================================
    print("\nPhase 8: Calculating $/Z rates...")
    hitter_pools = calc_dollars_per_z(hitter_pools)
    sp_pool = calc_dollars_per_z([sp_pool])[0]
    rp_pool = calc_dollars_per_z([rp_pool])[0]

    # ========================================================================
    # Phase 9: Stabilize hitter position assignments
    # ========================================================================
    print("\nPhase 9: Stabilizing hitter position assignments...")

    from ..domain.models import Player

    # Collect all unique hitters across pools (use dict to avoid hashability issues)
    all_hitters_dict: dict[str, Player] = {}
    for pool in hitter_pools:
        for player in (
            pool.rostered_players + pool.replacement_players + pool.below_replacement
        ):
            all_hitters_dict[player.id] = player

    all_hitters = list(all_hitters_dict.values())

    hitter_pools = stabilize_position_assignments(
        hitter_pools,
        all_hitters,
        budget_config,
        league_settings,
        league_budget,
    )

    # Update top-level computed fields from primary position valuation
    for player in all_hitters:
        if player.computed.primary_position in player.computed.valuations_by_position:
            primary_val = player.computed.valuations_by_position[
                player.computed.primary_position
            ]
            player.computed.raw_z = primary_val.raw_z.copy()
            player.computed.normalized_z = primary_val.normalized_z.copy()
            player.computed.total_z = primary_val.total_z
            player.computed.dollar_values = primary_val.dollar_values.copy()
            player.computed.total_dollars = primary_val.total_dollars
            player.computed.tier = primary_val.tier

    # ========================================================================
    # Phase 10: Build UTIL pool from stabilized replacement tiers + pure DHs
    # ========================================================================
    print("\nPhase 10: Building UTIL pool from stabilized pools...")

    import statistics

    from .valuation import get_player_stat

    # Calculate composite RLP archetype (RAW STATS) from stabilized primary positions
    composite_rlp_archetype: dict[str, float] = {}
    categories = league_settings["batting_categories"]

    for category in categories:
        position_rlp_means = []
        for pool in hitter_pools:
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

    # Build UTIL pool from replacement-tier players + pure DHs
    util_pool = build_util_pool(
        hitter_pools,
        pure_dh_players,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        budget_config,
    )

    # Iterate UTIL pool with composite RLP baseline
    print("  Iterating UTIL pool with composite RLP baseline...")
    util_pool = iterate_to_convergence(
        [util_pool],
        budget_config,
        league_settings,
        composite_rlp_archetype=composite_rlp_archetype,
    )[0]

    # Add UTIL to hitter pools
    hitter_pools.append(util_pool)

    # Re-allocate budgets across ALL hitter pools (including UTIL)
    print("  Re-allocating budgets with UTIL included...")
    hitter_pools = allocate_position_budgets(hitter_pools, league_budget, budget_config)
    hitter_pools = calc_dollars_per_z(hitter_pools)

    # Calculate dollar values for UTIL players
    from ..domain.models import PositionValuation

    for player in util_pool.rostered_players + util_pool.replacement_players:
        dollar_values = calc_player_dollars(player, util_pool)
        total_dollars = sum(dollar_values.values())

        if player in util_pool.rostered_players:
            tier = "ROSTERED"
        elif player in util_pool.replacement_players:
            tier = "REPLACEMENT"
        else:
            tier = "BELOW_REPLACEMENT"

        valuation = PositionValuation(
            position="UTIL",
            raw_z=player.computed.raw_z.copy(),
            normalized_z=player.computed.normalized_z.copy(),
            dollar_values=dollar_values,
            total_z=player.computed.total_z,
            total_dollars=total_dollars,
            tier=tier,  # type: ignore
        )
        player.computed.valuations_by_position["UTIL"] = valuation

    # ========================================================================
    # Phase 10.5: Value pitcher players (no multi-eligibility)
    # ========================================================================
    print("\nPhase 10.5: Calculating pitcher dollar values...")

    from ..domain.models import PositionValuation

    for pool in [sp_pool, rp_pool]:
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

            # Store position-specific valuation
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

            # Also update the main computed values
            player.computed.dollar_values = dollar_values
            player.computed.total_dollars = total_dollars
            player.computed.primary_position = pool.position

    all_pools = hitter_pools + [sp_pool, rp_pool]

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

    write_player_json(output_dir / "pitchers.json", pitchers_data, [sp_pool, rp_pool])
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
