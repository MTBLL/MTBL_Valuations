"""TRP (True Replacement Price) valuation engine - main pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from mtbl_valuations.domain.models import PositionPool, PositionValuation
from mtbl_valuations.engine.budget import (
    allocate_pool_budget,
    allocate_position_budgets,
    calc_league_budget,
    calc_pool_dollars_per_z,
)
from mtbl_valuations.engine.iteration import (
    iterate_to_convergence,
)
from mtbl_valuations.engine.pools import (
    build_pitcher_pool,
    build_position_pools,
    build_util_pool,
    dedupe_multi_position_players,
)
from mtbl_valuations.engine.valuation import distribute_player_dollars
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
    # Phase 3: Build position pools and iterate to convergence (multi-eligible)
    # ========================================================================
    print("\nPhase 3: Building hitter pools (multi-eligible)...")
    hitter_pools: dict[str, PositionPool] = build_position_pools(
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
        track_z_per_pool=True,  # Store Z-scores per position
    )

    # Dedupe: assign multi-position players to their best-ranked position
    print("  Deduplicating multi-position players...")
    hitter_pools, dedupe_changes = dedupe_multi_position_players(
        hitter_pools, budget_config
    )
    print(f"    Reassigned {dedupe_changes} players to primary positions")

    # Re-iterate after dedupe since pool composition changed
    if dedupe_changes > 0:
        print("  Re-iterating after dedupe...")
        hitter_pools = iterate_to_convergence(
            hitter_pools,
            budget_config,
            league_settings,
            track_z_per_pool=False,  # Now single-position mode
        )

    # ========================================================================
    # Phase 4: Build UTIL pool from stabilized replacement tiers + pure DHs
    # ========================================================================
    print("\nPhase 4: Building UTIL pool from stabilized pools...")

    # Build UTIL pool from replacement-tier players + pure DHs
    util_pool: PositionPool = build_util_pool(
        hitter_pools,
        pure_dh_players,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        budget_config,
    )

    # Iterate UTIL pool with composite RLP baseline
    print("  Iterating UTIL pool with composite RLP baseline...")
    util_pool = iterate_to_convergence(
        {"UTIL": util_pool},
        budget_config,
        league_settings,
    )["UTIL"]

    # Add UTIL to hitter pools
    hitter_pools["UTIL"] = util_pool

    # ========================================================================
    # Phase 5: Allocate hitter budgets
    # ========================================================================
    print("\nPhase 5: Allocating hitter budgets...")

    hitter_pools = allocate_position_budgets(hitter_pools, league_budget, budget_config)
    hitter_pools = calc_pool_dollars_per_z(hitter_pools)

    # Distribute dollars to all hitter players
    for pos, pool in hitter_pools.items():
        for player in pool.rostered_players + pool.replacement_players:
            dollar_values = distribute_player_dollars(player, pool)
            total_dollars = sum(dollar_values.values())

            # Store dollars on the player's valuation
            player.valuation.dollar_values = dollar_values
            player.valuation.total_dollars = total_dollars

    # ========================================================================
    # Phase 6: Build pitcher pools
    # ========================================================================
    print("\nPhase 6: Building pitcher pools...")
    sp_pool: dict[str, PositionPool] = {
        "SP": build_pitcher_pool(
            starters,
            league_settings["roster_slots"],
            league_settings["num_teams"],
            "SP",
            budget_config,
        )
    }
    print("  Iterating SP pool to convergence...")
    sp_pool = iterate_to_convergence(sp_pool, budget_config, league_settings)

    rp_pool: dict[str, PositionPool] = {
        "RP": build_pitcher_pool(
            relievers,
            league_settings["roster_slots"],
            league_settings["num_teams"],
            "RP",
            budget_config,
        )
    }
    print("  Iterating RP pool to convergence...")
    rp_pool = iterate_to_convergence(rp_pool, budget_config, league_settings)

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
    # Phase 9: Value pitcher players (no multi-eligibility)
    # ========================================================================
    print("\nPhase 9: Calculating pitcher dollar values...")
    pitchers = sp_pool | rp_pool

    for _, pool in pitchers.items():
        for rank, player in enumerate(pool.rostered_players + pool.replacement_players):
            # Calculate dollar values for THIS position
            dollar_values = distribute_player_dollars(player, pool)
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
                normalized_z=player.valuation.normalized_z.copy(),
                total_z=player.valuation.total_z,
                tier=tier,  # type: ignore
                position_rank=rank,
            )
            player.valuation.valuations_by_position[pool.position] = valuation

            # Also update the main computed values
            player.valuation.dollar_values = dollar_values
            player.valuation.total_dollars = total_dollars
            player.valuation.primary_position = pool.position

    all_pools = hitter_pools | sp_pool | rp_pool

    # ========================================================================
    # Phase 10: Validate
    # ========================================================================
    print("\nPhase 10: Validation...")
    validate_budget_balance(all_pools, league_budget)
    validate_tier_counts(
        all_pools, league_settings["roster_slots"], league_settings["num_teams"]
    )
    validate_rlp_z_scores(all_pools)

    # ========================================================================
    # Phase 11: Output
    # ========================================================================
    print("\nPhase 11: Writing output files...")

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
