"""TRP (True Replacement Price) valuation engine - main pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from mtbl_valuations.domain.models import PositionPool
from mtbl_valuations.engine.budget import (
    allocate_pool_budget,
    allocate_position_budgets,
    calc_league_budget,
    calc_pool_dollars_per_z,
)
from mtbl_valuations.engine.iteration import (
    finalize_pool_player_valuations,
    iterate_to_convergence_global,
    iterate_to_convergence_per_position,
)
from mtbl_valuations.engine.pools import (
    assign_primary_position_from_pool,
    build_pitcher_pool,
    build_position_pools,
    build_util_pool,
    dedupe_multi_position_players,
)
from mtbl_valuations.engine.valuation import distribute_pool_dollars
from mtbl_valuations.io.exports import export_detailed_position_csvs
from mtbl_valuations.io.loader import (
    load_batters,
    load_budget_config,
    load_league_settings,
    load_pitchers,
)
from mtbl_valuations.io.writers import (
    write_player_json,
    write_position_summary_csv,
    write_valuations_csv,
)
from mtbl_valuations.validation.checks import (
    validate_budget_balance,
    validate_position_valuation_hydration,
    validate_rlp_z_scores,
    validate_tier_counts,
)


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
    league_budget = calc_league_budget(league_settings, budget_config)

    ros_slots = league_settings["roster_slots"]
    num_teams = league_settings["num_teams"]
    rlp_tier_pct = budget_config["replacement_tier_pct"]
    min_rlp_tier_size = budget_config["min_replacement_tier_size"]

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

    # Identify pure DH players (only eligible for DH/UTIL, no pitcher eligibility)
    pure_dh_players = [
        h
        for h in hitters
        if set(h.positions).issubset({"DH", "UTIL"}) or h.name == "Shohei Ohtani"
    ]

    # Regular hitters (not pure DH)
    regular_hitters = [h for h in hitters if h not in pure_dh_players]

    print(f"  Regular hitters: {len(regular_hitters)}")
    print(f"  Pure DH players: {len(pure_dh_players)}")
    print(f"  Starters: {len(starters)}")
    print(f"  Relievers: {len(relievers)}")

    # ========================================================================
    # Phase 3: Build position pools and iterate to convergence
    # ========================================================================
    # Phase 3a
    print("\nPhase 3: Building hitter pools (multi-eligible)...")
    hitter_pools: dict[str, PositionPool] = build_position_pools(
        regular_hitters,
        ros_slots,
        num_teams,
        "HITTER",
        rlp_tier_pct,
        min_rlp_tier_size,
        use_eligibility=True,  # Players appear in ALL eligible positions
    )

    # Phase 3b
    print("  Iterating hitter pools to convergence (per-pool tracking)...")
    hitter_pools = iterate_to_convergence_per_position(
        hitter_pools,
        budget_config,
        league_settings,
    )

    # Phase 3c
    # Dedupe: assign multi-position players to their best-ranked position
    print("  Deduplicating multi-position players...")
    hitter_pools, dedupe_changes = dedupe_multi_position_players(
        hitter_pools, rlp_tier_pct, min_rlp_tier_size
    )
    print(f"    Reassigned {dedupe_changes} players to primary positions")

    # Phase 3d
    # Re-iterate after dedupe since pool composition changed
    if dedupe_changes > 0:
        print("  Re-iterating after dedupe...")
        hitter_pools = iterate_to_convergence_global(
            hitter_pools,
            budget_config,
            league_settings,
        )

    # ========================================================================
    # Phase 4: Build UTIL pool from stabilized replacement tiers + pure DHs
    # ========================================================================
    print("\nPhase 4: Building UTIL pool from stabilized pools...")

    # Phase 4a
    # Build UTIL pool from replacement-tier players + pure DHs
    util_pool: PositionPool = build_util_pool(
        hitter_pools,
        pure_dh_players,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        rlp_tier_pct,
        min_rlp_tier_size,
    )

    # Phase 4b
    # Iterate UTIL pool
    print("  Iterating UTIL pool with composite RLP baseline...")
    util_pool = iterate_to_convergence_per_position(
        {"UTIL": util_pool},
        budget_config,
        league_settings,
    )["UTIL"]

    # Phase 4c
    # Finalize UTIL pool player valuations (primary position, Z-scores, tiers)
    print("  Finalizing UTIL pool player valuations...")
    finalize_pool_player_valuations(util_pool)

    # Add UTIL to hitter pools
    # Note: UTIL players remain in their original position pools (for exports)
    hitter_pools["UTIL"] = util_pool

    # ========================================================================
    # Phase 5: Allocate hitter budgets
    # ========================================================================
    print("\nPhase 5: Allocating hitter budgets...")

    hitter_pools = allocate_position_budgets(hitter_pools, league_budget, budget_config)
    hitter_pools = calc_pool_dollars_per_z(hitter_pools)
    distribute_pool_dollars(hitter_pools, store_per_position=True)

    # Validate position valuations are hydrated
    validate_position_valuation_hydration(hitter_pools)

    # ========================================================================
    # Phase 6: Build pitcher pools
    # ========================================================================
    # Phase 6a
    print("\nPhase 6: Building pitcher pools...")
    sp_pool: dict[str, PositionPool] = {
        "SP": build_pitcher_pool(
            starters,
            league_settings["roster_slots"],
            league_settings["num_teams"],
            "SP",
            rlp_tier_pct,
            min_rlp_tier_size,
        )
    }
    # Phase 6b
    print("  Iterating SP pool to convergence...")
    sp_pool = iterate_to_convergence_global(sp_pool, budget_config, league_settings)

    # Phase 6c
    rp_pool: dict[str, PositionPool] = {
        "RP": build_pitcher_pool(
            relievers,
            league_settings["roster_slots"],
            league_settings["num_teams"],
            "RP",
            rlp_tier_pct,
            min_rlp_tier_size,
        )
    }
    # Phase 6d
    print("  Iterating RP pool to convergence...")
    rp_pool = iterate_to_convergence_global(rp_pool, budget_config, league_settings)

    # ========================================================================
    # Phase 7: Allocate pitcher budgets
    # ========================================================================
    print("\nPhase 7: Allocating pitcher budgets...")
    sp_pool.update(
        {
            "SP": allocate_pool_budget(
                sp_pool["SP"],
                league_budget.sp_budget,
                budget_config["sp_category_weights"],
            )
        }
    )
    rp_pool.update(
        {
            "RP": allocate_pool_budget(
                rp_pool["RP"],
                league_budget.rp_budget,
                budget_config["rp_category_weights"],
            )
        }
    )
    sp_pool.update(calc_pool_dollars_per_z(sp_pool))
    rp_pool.update(calc_pool_dollars_per_z(rp_pool))

    # ========================================================================
    # Phase 8: Distribute pitcher players budgets (no multi-eligibility)
    # ========================================================================
    print("\nPhase 8: Calculating pitcher dollar values...")
    pitchers = sp_pool | rp_pool

    # Assign primary positions for pitcher pools
    for _, pool in pitchers.items():
        assign_primary_position_from_pool(pool)

    # Distribute dollars to all pitcher players
    distribute_pool_dollars(pitchers, store_per_position=False)

    # ========================================================================
    # Phase 9: Validate
    # ========================================================================
    print("\nPhase 9: Validation...")

    all_pools = hitter_pools | sp_pool | rp_pool
    validate_budget_balance(all_pools, league_budget)
    validate_tier_counts(
        all_pools, league_settings["roster_slots"], league_settings["num_teams"]
    )
    validate_rlp_z_scores(all_pools)

    # ========================================================================
    # Phase 10: Output
    # ========================================================================
    print("\nPhase 10: Writing output files...")

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

    write_player_json(output_dir / "pitchers.json", pitchers_data, sp_pool | rp_pool)
    print(f"  ✓ Wrote {output_dir / 'pitchers.json'}")

    # Export detailed position-specific CSVs
    export_detailed_position_csvs(
        hitter_pools,
        sp_pool["SP"],
        rp_pool["RP"],
        output_dir,
        league_settings["batting_categories"],
        league_settings["pitching_categories"],
    )

    print("\n=== TRP Valuation Complete ===")
