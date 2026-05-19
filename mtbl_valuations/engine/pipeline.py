"""TRP (True Replacement Price) valuation engine - main pipeline."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from mtbl_valuations.domain.models import PositionPool
from mtbl_valuations.engine.iteration_logger import (
    IterationLogger,
    parse_iter_log_level,
    pop_logger,
    pop_phase,
    push_logger,
    push_phase,
)
from mtbl_valuations.engine.budget import (
    allocate_pool_budget,
    allocate_position_budgets,
    calc_league_budget,
    calc_pool_dollars_per_z,
)
from mtbl_valuations.domain.models import LeagueBudget, Player
from mtbl_valuations.engine.iteration import (
    finalize_pool_player_valuations,
    iterate_to_convergence_global,
    iterate_to_convergence_per_position,
    recompute_pool_z_in_place,
    sync_pool_z_to_position,
)
from mtbl_valuations.engine.pools import (
    assign_primary_position_from_pool,
    build_pitcher_pool,
    build_position_pools,
    build_util_pool,
    dedupe_multi_position_players,
)
from mtbl_valuations.engine.valuation import (
    distribute_pool_dollars,
    get_categories,
    get_player_stat,
)
from mtbl_valuations.io.current import (
    load_batters_current,
    load_pitchers_current,
)
from mtbl_valuations.io.loader import (
    ValuationSource,
    load_batters,
    load_budget_config,
    load_league_settings,
    load_pitchers,
)
from mtbl_valuations.io.qualified import compute_qualified_pa
from mtbl_valuations.io.savant_ranks import inject_savant_pct_rnks
from mtbl_valuations.io.synthetic import (
    load_batters_synthetic,
    load_pitchers_synthetic,
)
from mtbl_valuations.io.writers import (
    build_player_valuations,
    write_merged_player_json,
    write_position_summary_csv,
)

# Valuation sources mapped to the output-subdir / JSON-key label used in the
# merged multi-source outputs. The first three are raw Fangraphs projection
# sets; "synthetic" is derived from Statcast data (see io/synthetic.py);
# "current" values current-season actuals (see io/current.py).
SOURCE_LABELS: dict[ValuationSource, str] = {
    "projections": "preseason",
    "projs_updated": "updated",
    "ros": "ros",
    "synthetic": "synthetic",
    "current": "current",
}
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
    source: ValuationSource = "projections",
    iter_logger: IterationLogger | None = None,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    set[str],
    set[str],
]:
    """
    Run the complete TRP valuation pipeline for a single valuation source.
    This is the 12-phase pipeline from the architecture document.

    Args:
        source: Which valuation source to value against — a Fangraphs
            projection set or the Statcast-derived "synthetic" source.
            Players with no data for the source are skipped by the loader.
        iter_logger: Optional per-source IterationLogger. When provided, the
            hitter convergence loops (Phase 3b / 3d / 4b) and the Phase 5
            budget snapshot are dumped to per-position log files.

    Returns:
        (hitter_valuations, pitcher_valuations) - each a mapping of player id
        to the serialized valuation payload, for merging across sources.
    """
    print("=== TRP Valuation Engine ===\n")

    # Bind the iteration logger to the ContextVar for the rest of this call.
    # The iteration / convergence loops in engine/iteration.py read it via
    # current_logger(); pitcher phases never push a phase, so their iter
    # calls fall outside the logger's phase whitelist and no-op.
    log_token = push_logger(iter_logger) if iter_logger is not None else None
    try:
        return _run_trp_valuation_inner(
            batters_file,
            pitchers_file,
            league_file,
            budget_config_file,
            output_dir,
            source,
            iter_logger,
        )
    finally:
        if log_token is not None:
            pop_logger(log_token)


def _run_trp_valuation_inner(
    batters_file: Path,
    pitchers_file: Path,
    league_file: Path,
    budget_config_file: Path,
    output_dir: Path,
    source: ValuationSource,
    iter_logger: IterationLogger | None,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    set[str],
    set[str],
]:

    # ========================================================================
    # Phase 1: Initialize
    # ========================================================================
    print(f"Phase 1: Loading data (valuation source: {source})...")
    league_settings = load_league_settings(league_file)
    budget_config = load_budget_config(budget_config_file)
    league_budget = calc_league_budget(league_settings, budget_config)

    if source == "synthetic":
        # Synthetic stats are built from Statcast data; the loader needs the
        # budget config for its blend coefficients and the sliding qualified
        # threshold for its sample gate.
        qualified_pa = compute_qualified_pa(batters_file, budget_config)
        hitter_players = load_batters_synthetic(
            batters_file, budget_config, qualified_pa
        )
        pitcher_players = load_pitchers_synthetic(
            pitchers_file, budget_config, qualified_pa
        )
    elif source == "current":
        # Current-season actuals, gated by the sliding qualified threshold.
        qualified_pa = compute_qualified_pa(batters_file, budget_config)
        hitter_players = load_batters_current(batters_file, qualified_pa)
        pitcher_players = load_pitchers_current(pitchers_file, qualified_pa)
    else:
        # mypy narrows `source` to ProjectionSource in this branch.
        # Stub-projection guards: drop call-up / partial-season lines with
        # tiny PA/IP that would otherwise leak elite rate stats into the
        # rostered tier. ``current`` and ``synthetic`` apply their own
        # sliding qualified gates, so they don't need this.
        qualified_cfg = budget_config.get("qualified", {}) or {}
        min_proj_pa = float(qualified_cfg.get("min_projection_pa", 0.0))
        min_proj_ip = float(qualified_cfg.get("min_projection_ip", 0.0))
        hitter_players = load_batters(
            batters_file, source, min_projection_pa=min_proj_pa
        )
        pitcher_players = load_pitchers(
            pitchers_file, source, min_projection_ip=min_proj_ip
        )

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
    phase_token = push_phase("phase3b-iter")
    try:
        hitter_pools = iterate_to_convergence_per_position(
            hitter_pools,
            budget_config,
            league_settings,
        )
    finally:
        pop_phase(phase_token)

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
        phase_token = push_phase("phase3d-reiter")
        try:
            hitter_pools = iterate_to_convergence_global(
                hitter_pools,
                budget_config,
                league_settings,
            )
        finally:
            pop_phase(phase_token)
        # The global re-iteration refreshes only the top-level Z-scores;
        # mirror them into valuations_by_position so the per-position dollar
        # distribution in Phase 5 stays consistent with the $/Z rates.
        sync_pool_z_to_position(hitter_pools)

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
    phase_token = push_phase("phase4b-util")
    try:
        util_pool = iterate_to_convergence_per_position(
            {"UTIL": util_pool},
            budget_config,
            league_settings,
        )["UTIL"]
    finally:
        pop_phase(phase_token)

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

    # Phase 5 finalization swap-pass: rank-by-z and rank-by-dollar can
    # diverge when ``$/Z`` weighting interacts with category mix. If any
    # RLP player out-prices the lowest rostered, swap the pair, refresh
    # every pool's averages / settled z / dollar-proxy rank, and re-run
    # Phase 5. Loop until stable.
    swap_count = _resolve_hitter_dollar_misallocations(
        hitter_pools, league_budget, budget_config, league_settings
    )
    if swap_count:
        print(
            f"  Phase 5 finalization: resolved {swap_count} dollar mis-allocations"
        )

    # Phase 5 budget snapshot — one log per pool with category budgets,
    # $/Z, baseline shifts and per-player dollars. league_raw / league_budget
    # are SUMS across every rostered hitter / every pool's budget across the
    # league — gives the per-pool log a "this pool's slice of the league"
    # anchor against the pos_raw / pos_budget columns.
    if iter_logger is not None:
        hitter_cats = league_settings["batting_categories"]
        league_raw_sum: dict[str, float] = {
            c: sum(
                get_player_stat(p, c)
                for pool in hitter_pools.values()
                for p in pool.rostered_players
            )
            for c in hitter_cats
        }
        league_budget_sum: dict[str, float] = {
            c: sum(
                pool.category_budgets.get(c, 0.0) for pool in hitter_pools.values()
            )
            for c in hitter_cats
        }
        for pool in hitter_pools.values():
            iter_logger.log_budget(
                pool,
                "phase5-budget",
                per_position=True,
                categories=hitter_cats,
                league_raw=league_raw_sum,
                league_budget=league_budget_sum,
            )

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
    phase_token = push_phase("phase6b-iter")
    try:
        sp_pool = iterate_to_convergence_global(
            sp_pool, budget_config, league_settings
        )
    finally:
        pop_phase(phase_token)

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
    phase_token = push_phase("phase6d-iter")
    try:
        rp_pool = iterate_to_convergence_global(
            rp_pool, budget_config, league_settings
        )
    finally:
        pop_phase(phase_token)

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

    # Phase 8 budget snapshot — per-pool log for SP and RP. Pitchers don't
    # share budget across pools (allocate_pool_budget runs independently
    # per pool), so league_raw / league_budget are omitted; the per-pool
    # totals already tell the whole story.
    if iter_logger is not None:
        for pos, pool in pitchers.items():
            iter_logger.log_budget(
                pool,
                "phase8-budget",
                per_position=False,
                categories=get_categories(pool.role, league_settings),
            )

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
    # Per-source pipeline only writes ``position_summary.csv`` — the
    # pool-level aggregates (budget_*, dollars_per_z_*, replacement_baseline_*)
    # aren't carried in the merged player JSON. Everything else
    # (per-player valuations, z-scores, dollars, raw projection / savant
    # diagnostics) lands in the top-level merged ``hitters.json`` /
    # ``pitchers.json`` via ``run_all_valuations``, so per-source
    # valuations.csv / *_detailed.csv / hitters.json / pitchers.json are
    # not written.
    print("\nPhase 10: Writing output files...")

    output_dir.mkdir(parents=True, exist_ok=True)

    write_position_summary_csv(output_dir / "position_summary.csv", all_pools)
    print(f"  ✓ Wrote {output_dir / 'position_summary.csv'}")

    print("\n=== TRP Valuation Complete ===")

    # Per-player valuation payloads, returned so callers can merge across
    # projection sources into a single enriched JSON.
    hitter_valuations = build_player_valuations(hitter_pools)
    pitcher_valuations = build_player_valuations(sp_pool | rp_pool)

    # Rostered + RLP id sets — the "settled fantasy universe" for this
    # source. ``run_all_valuations`` uses the current source's sets as
    # the population for savant pct_rnk computation.
    hitter_rostered_rlp_ids: set[str] = {
        p.id
        for pool in hitter_pools.values()
        for p in pool.rostered_players + pool.replacement_players
    }
    pitcher_rostered_rlp_ids: set[str] = {
        p.id
        for pool in (sp_pool | rp_pool).values()
        for p in pool.rostered_players + pool.replacement_players
    }
    return (
        hitter_valuations,
        pitcher_valuations,
        hitter_rostered_rlp_ids,
        pitcher_rostered_rlp_ids,
    )


# Hitter pools handled by the Phase 5 swap-pass. UTIL is included
# because Phase 4c sets primary_position=UTIL on every UTIL pool player
# regardless of tier, so swapping within UTIL doesn't introduce
# primary_position drift — the swap just moves a player between UTIL's
# own rostered and replacement tiers.
#
# Pitcher pools (SP / RP) deliberately do NOT get a swap-pass: they
# allocate budget per-pool (no cross-pool weighted dollar proxy) and
# the stub-projection PA/IP gate at the loader boundary keeps the iter
# loop converging cleanly. Empirically: zero ``rlp_outprices`` warnings
# across all 5 valuation sources. Add a swap-pass only if a future
# source surfaces SP/RP mis-allocations.
# Tuple (not frozenset) for deterministic iteration order — the swap loop
# re-runs Phase 5's dollar math after each pool's pair-swap, so iteration
# order influences which pool's averages get refreshed first and therefore
# which subsequent swaps trigger. Deterministic order keeps runs reproducible.
_SWAP_PASS_POSITIONS: tuple[str, ...] = ("C", "1B", "2B", "3B", "SS", "OF", "UTIL")


def _resolve_hitter_dollar_misallocations(
    hitter_pools: dict[str, PositionPool],
    league_budget: LeagueBudget,
    budget_config: dict[str, Any],
    league_settings: dict[str, Any],
    max_passes: int = 30,
) -> int:
    """Phase 5 finalization: pair-swap any rostered/RLP players where the
    RLP player out-prices the lowest rostered, then refresh derived pool
    state and re-run Phase 5 dollar distribution. Loop until stable.

    Returns the total number of swaps performed across all passes.
    """

    def dollars_of(player: Player, pos: str) -> float:
        pv = player.valuation.valuations_by_position.get(pos)
        return pv.total_dollars if pv else player.valuation.total_dollars

    # Filter once: leagues with no UTIL slot (or any other missing
    # configured swap position) drop here, so the inner loops don't need
    # per-iteration None / empty-tier guards. Both tiers must be populated
    # for the swap to be meaningful (min/max over empty lists raises).
    swap_positions = [
        pos
        for pos in _SWAP_PASS_POSITIONS
        if (pool := hitter_pools.get(pos)) is not None
        and pool.rostered_players
        and pool.replacement_players
    ]

    total_swaps = 0
    for _ in range(max_passes):
        any_swap = False
        for pos in swap_positions:
            pool = hitter_pools[pos]
            rost_min = min(
                pool.rostered_players, key=lambda p: dollars_of(p, pos)
            )
            rlp_max = max(
                pool.replacement_players, key=lambda p: dollars_of(p, pos)
            )
            if dollars_of(rlp_max, pos) > dollars_of(rost_min, pos):
                pool.rostered_players.remove(rost_min)
                pool.replacement_players.remove(rlp_max)
                pool.rostered_players.append(rlp_max)
                pool.replacement_players.append(rost_min)
                any_swap = True
                total_swaps += 1

        if not any_swap:
            return total_swaps

        # Refresh derived state for every swappable pool. UTIL is left as
        # Phase 4 settled it, but it still participates in the Phase 5
        # cross-pool allocation below since other pools' rostered changed.
        for pos in swap_positions:
            recompute_pool_z_in_place(
                hitter_pools[pos],
                hitter_pools,
                budget_config,
                league_settings,
            )

        # Re-run the dollar math against the new rostered compositions.
        allocate_position_budgets(hitter_pools, league_budget, budget_config)
        calc_pool_dollars_per_z(hitter_pools)
        distribute_pool_dollars(hitter_pools, store_per_position=True)

    return total_swaps


def run_all_valuations(
    batters_file: Path,
    pitchers_file: Path,
    league_file: Path,
    budget_config_file: Path,
    output_dir: Path,
    iter_log_level: str | None = None,
    logs_dir: Path = Path("logs"),
) -> None:
    """
    Run the TRP valuation for every Fangraphs projection source.

    Each source's CSV outputs land in its own subdirectory of ``output_dir``
    (preseason/, updated/, ros/). A single merged ``hitters.json`` /
    ``pitchers.json`` is written at ``output_dir`` with each player's
    valuations keyed by source label.

    Args:
        iter_log_level: When ``"INSIGHTS"`` or ``"DEBUG"``, dumps per-iteration
            tabular logs for the hitter convergence phases (3b / 3d / 4b) and
            the Phase 5 budget snapshot, one file per (source, phase, pos).
            ``None`` (the library default) writes no iteration logs — keeps
            tests / programmatic callers quiet.
        logs_dir: Root directory under which a timestamped run dir is
            created when iter logging is enabled.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    log_level_num = parse_iter_log_level(iter_log_level)
    iter_run_dir: Path | None = None
    if log_level_num is not None:
        run_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        iter_run_dir = logs_dir / run_stamp
        iter_run_dir.mkdir(parents=True, exist_ok=True)
        print(f"Iteration logs: {iter_run_dir}  (level={iter_log_level})")

    hitter_vals_by_source: dict[str, dict[str, dict[str, Any]]] = {}
    pitcher_vals_by_source: dict[str, dict[str, dict[str, Any]]] = {}

    # Captured for the savant pct_rnk pass — savant data is observed
    # (source-independent), so the population is the *current* source's
    # rostered + RLP across all pools.
    current_hitter_ids: set[str] = set()
    current_pitcher_ids: set[str] = set()

    for source, label in SOURCE_LABELS.items():
        print(f"\n{'#' * 70}")
        print(f"# Projection source: {label}  ({source})")
        print(f"{'#' * 70}\n")
        iter_logger: IterationLogger | None = None
        if iter_run_dir is not None and log_level_num is not None:
            iter_logger = IterationLogger(iter_run_dir, label, log_level_num)
        (
            hitter_valuations,
            pitcher_valuations,
            hitter_ids,
            pitcher_ids,
        ) = run_trp_valuation(
            batters_file,
            pitchers_file,
            league_file,
            budget_config_file,
            output_dir / label,
            source,
            iter_logger=iter_logger,
        )
        hitter_vals_by_source[label] = hitter_valuations
        pitcher_vals_by_source[label] = pitcher_valuations
        if source == "current":
            current_hitter_ids = hitter_ids
            current_pitcher_ids = pitcher_ids
        if iter_logger is not None:
            iter_logger.finalize_summary()

    # Merged enriched JSON across all sources
    print("\nWriting merged multi-source player JSON...")
    with open(batters_file) as f:
        batters_data = json.load(f)
    with open(pitchers_file) as f:
        pitchers_data = json.load(f)

    # Inject pct_rnks into stats.savant.* nested objects using the current
    # source's rostered+RLP universe as the ranking population. Mutates
    # records in place so the enriched savant blocks flow through to the
    # merged JSON below.
    if current_hitter_ids or current_pitcher_ids:
        h_ranked, p_ranked = inject_savant_pct_rnks(
            batters_data,
            pitchers_data,
            current_hitter_ids,
            current_pitcher_ids,
        )
        print(
            f"  Savant pct_rnks: {h_ranked} hitter fields, {p_ranked} pitcher "
            f"fields (population: current-source rostered + RLP)"
        )

    write_merged_player_json(
        output_dir / "hitters.json", batters_data, hitter_vals_by_source
    )
    print(f"  ✓ Wrote {output_dir / 'hitters.json'}")
    write_merged_player_json(
        output_dir / "pitchers.json", pitchers_data, pitcher_vals_by_source
    )
    print(f"  ✓ Wrote {output_dir / 'pitchers.json'}")
