from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mtbl_valuations.engine.budget import (
    allocate_pool_budget,
    allocate_position_budgets,
    calc_pool_dollars_per_z,
)
from mtbl_valuations.engine.iteration import (
    iterate_to_convergence_global,
    iterate_to_convergence_per_position,
    sync_pool_z_to_position,
)
from mtbl_valuations.engine.pipeline import (
    run_all_valuations,
    run_trp_valuation,
    validate_position_valuation_hydration,
)
from mtbl_valuations.engine.pools import (
    build_pitcher_pool,
    build_util_pool,
    dedupe_multi_position_players,
)
from mtbl_valuations.engine.valuation import (
    distribute_player_dollars,
    distribute_pool_dollars,
)
from mtbl_valuations.validation.checks import (
    validate_budget_balance,
    validate_rlp_z_scores,
    validate_tier_counts,
)

if TYPE_CHECKING:
    from mtbl_valuations.domain import LeagueBudget, PositionPool


class TestPipeline:
    def test_pipeline(
        self, batters_file, pitchers_file, league_file, budget_config_file, tmp_path
    ):
        """Test that the full pipeline runs without errors."""
        # Single-source ``run_trp_valuation`` only writes the pool-level
        # ``position_summary.csv``. The merged hitters/pitchers JSON +
        # per-source CSVs were removed — see commit dropping redundant
        # outputs (everything lives in the merged JSON written by
        # ``run_all_valuations``).
        run_trp_valuation(
            batters_file, pitchers_file, league_file, budget_config_file, tmp_path
        )

        assert (tmp_path / "position_summary.csv").exists()

    def test_run_all_valuations_with_iter_logging_writes_logs(
        self, batters_file, pitchers_file, league_file, budget_config_file, tmp_path
    ):
        """When ``iter_log_level`` is set, a timestamped logs dir is created
        with per-source iteration files + a ``{source}_summary.log`` per source.
        Covers the iter_logger branches inside the pipeline that the default
        run silently skips."""
        out_dir = tmp_path / "out"
        logs_dir = tmp_path / "logs"
        run_all_valuations(
            batters_file,
            pitchers_file,
            league_file,
            budget_config_file,
            out_dir,
            iter_log_level="INSIGHTS",
            logs_dir=logs_dir,
        )
        # Exactly one timestamped run dir under logs_dir.
        run_dirs = list(logs_dir.iterdir())
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]
        # Per-source summary files exist for every source we ran.
        for label in ("preseason", "updated", "ros", "synthetic", "current"):
            assert (run_dir / f"{label}_summary.log").exists()
            # At least one position dump landed for the hitter convergence
            # phases (SS is always populated in a 10-team league).
            assert (run_dir / label / "SS.log").exists()

    def test_run_all_valuations_multi_source(
        self, batters_file, pitchers_file, league_file, budget_config_file, tmp_path
    ):
        """run_all_valuations runs the pipeline once per valuation source,
        writing per-source CSV subdirs plus a merged JSON keyed by source label."""
        run_all_valuations(
            batters_file, pitchers_file, league_file, budget_config_file, tmp_path
        )

        # Each source gets its own subdirectory containing position_summary.csv
        # (pool-level aggregates not carried in the merged JSON). Per-source
        # valuations.csv / *_detailed.csv / hitters.json / pitchers.json were
        # dropped — that data lives in the top-level merged JSON's
        # ``valuations[source]`` blocks.
        sources = ("preseason", "updated", "ros", "synthetic", "current")
        for label in sources:
            assert (tmp_path / label / "position_summary.csv").exists()

        # A single merged JSON sits at the top level, with each player's
        # valuations nested by source label.
        merged = json.loads((tmp_path / "hitters.json").read_text())
        assert (tmp_path / "pitchers.json").exists()
        valued = [rec for rec in merged if "valuations" in rec]
        assert valued, "expected at least one player with merged valuations"
        # Every source label present on a record must be one we ran.
        for rec in valued:
            assert set(rec["valuations"]).issubset(set(sources))


class TestSwapPassUnit:
    """Direct unit tests on ``_resolve_hitter_dollar_misallocations``."""

    def test_returns_zero_when_no_swap_needed(self, league_settings, budget_config):
        """Pool where every rostered player already out-prices every RLP
        player triggers the no-swap path: function returns 0 on the first
        pass without invoking any refresh."""
        from mtbl_valuations.domain.models import (
            HitterStats,
            LeagueBudget,
            Player,
            PositionPool,
            Valuation,
        )
        from mtbl_valuations.engine.pipeline import (
            _resolve_hitter_dollar_misallocations,
        )

        def _mk(pid: str, dollars: float) -> Player:
            v = Valuation()
            v.total_dollars = dollars
            return Player(
                id=pid,
                name=pid,
                team="T",
                positions=["SS"],
                role="HITTER",
                stats=HitterStats(
                    pa=600,
                    ab=540,
                    r=80,
                    hr=20,
                    rbi=70,
                    sbn=10,
                    obp=0.350,
                    slg=0.450,
                ),
                valuation=v,
            )

        # rostered player has higher dollars than the RLP candidate → no swap.
        rost = _mk("rost", dollars=10.0)
        rlp = _mk("rlp", dollars=2.0)
        pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
        pool.rostered_players = [rost]
        pool.replacement_players = [rlp]

        league_budget = LeagueBudget(
            total=2600,
            hitter_budget=1820,
            pitcher_budget=780,
            sp_budget=390,
            rp_budget=390,
        )

        swaps = _resolve_hitter_dollar_misallocations(
            {"SS": pool}, league_budget, budget_config, league_settings
        )
        assert swaps == 0
        # Pool composition unchanged.
        assert pool.rostered_players == [rost]
        assert pool.replacement_players == [rlp]

    def test_sync_primary_skips_missing_pool(self):
        """``_sync_primary_to_rostered_base_pool`` must tolerate leagues
        whose hitter_pools dict is missing one of the configured base
        positions (e.g. a league with no catcher slot)."""
        from mtbl_valuations.domain.models import PositionPool
        from mtbl_valuations.engine.pipeline import (
            _sync_primary_to_rostered_base_pool,
        )

        # Only 1B is present — every other base position lookup returns
        # None and the inner continue branch must fire.
        pool_1b = PositionPool(position="1B", role="HITTER", roster_slots=1)
        # Empty pool to make the function a no-op past the missing-pool
        # guards; we're testing the guard itself, not any sync work.
        _sync_primary_to_rostered_base_pool({"1B": pool_1b})


class TestPipelinePhase3RegularHitters:
    def test_pipeline_before_dedupe(self, converged_hitter_pools, league_settings):
        """Test that the pipeline at point Phase 3b."""
        num_teams = league_settings["num_teams"]

        hitter_pools = converged_hitter_pools

        assert hitter_pools is not None
        for pos, pool in hitter_pools.items():
            assert pool is not None
            if pos == "OF":
                assert len(pool.rostered_players) == num_teams * 3
            else:
                assert len(pool.rostered_players) == num_teams
            assert len(pool.replacement_players) >= 3
        assert "UTIL" not in hitter_pools.keys()

    def test_pipeline_dedeupe_phase3c(self, converged_hitter_pools, league_settings):
        """Instead of importing the cached fixture, we run the dedupe function"""
        num_teams = league_settings["num_teams"]
        hitter_pools, dedupe_changes = dedupe_multi_position_players(
            converged_hitter_pools, 0.03, 3
        )

        assert hitter_pools is not None
        assert dedupe_changes > 0
        for pos, pool in hitter_pools.items():
            assert pool is not None
            if pos == "OF":
                assert len(pool.rostered_players) == num_teams * 3
            else:
                assert len(pool.rostered_players) == num_teams

            for check_pos, check_pool in hitter_pools.items():
                if check_pos == pos:
                    continue
                for player in check_pool.rostered_players:
                    assert player not in pool.rostered_players

            assert len(pool.replacement_players) >= 3
        assert "UTIL" not in hitter_pools.keys()

    def test_pipeline_post_dedupe_phase3d(
        self,
        converged_hitter_pools_deduped: tuple[dict[str, PositionPool], int],
        budget_config,
        league_settings,
    ):
        deduped, num_dedupes = converged_hitter_pools_deduped
        num_teams = league_settings["num_teams"]
        if num_dedupes > 0:
            print("  Re-iterating after dedupe...")
            hitter_pools = iterate_to_convergence_global(
                deduped,
                budget_config,
                league_settings,
            )

            assert hitter_pools is not None
            for pos, pool in hitter_pools.items():
                assert pool is not None
                if pos == "OF":
                    assert len(pool.rostered_players) == num_teams * 3
                else:
                    assert len(pool.rostered_players) == num_teams

                # assert no players are duplicated across positions
                for check_pos, check_pool in hitter_pools.items():
                    if check_pos == pos:
                        continue
                    for player in check_pool.rostered_players:
                        assert player not in pool.rostered_players

                # Tier composition is the key invariant; sort order
                # within rostered is by the internal dollar-proxy rank
                # metric, not by total_z (which is the intuitive sum of
                # settled per-cat z's).

                assert len(pool.replacement_players) >= 3
                for player in pool.rostered_players:
                    assert player.valuation.tier == "ROSTERED"
                for player in pool.replacement_players:
                    assert player.valuation.tier == "REPLACEMENT"
                for player in pool.below_replacement:
                    assert player.valuation.tier == "BELOW_REPLACEMENT"
            assert "UTIL" not in hitter_pools.keys()


class TestPipelinePhase4Util:
    def test_pipeline_build_util_pool_phase4a(
        self,
        dh_and_regular_hitters,
        hitter_pools_deduped_converged: dict[str, PositionPool],
        league_settings,
    ):
        pure_dh_hitters, _ = dh_and_regular_hitters
        util_pool = build_util_pool(
            hitter_pools_deduped_converged,
            pure_dh_hitters,
            roster_slots=league_settings["roster_slots"],
            num_teams=league_settings["num_teams"],
            rlp_tier_pct=0.03,
            min_rlp_tier_size=3,
        )
        assert util_pool is not None
        util_players = (
            util_pool.rostered_players
            + util_pool.replacement_players
            + util_pool.below_replacement
        )

        for _, pool in hitter_pools_deduped_converged.items():
            for player in pool.replacement_players + pool.below_replacement:
                assert player in util_players

        assert all(
            util_pool.rostered_players[i].stats.wrc_plus  # type: ignore
            >= util_pool.rostered_players[i + 1].stats.wrc_plus  # type: ignore
            for i in range(len(util_pool.rostered_players) - 1)
        )

    def test_pipeline_converge_util_pool_phase4b(
        self,
        util_pool_phase4a: PositionPool,
        hitter_pools_deduped_converged: dict[str, PositionPool],
        budget_config,
        league_settings,
    ):
        num_teams = league_settings["num_teams"]
        # Create a copy to avoid mutating the session-scoped fixture
        hitter_pools = dict(hitter_pools_deduped_converged)
        # Phase 4b consumes each pool's replacement tier to build the UTIL pool,
        # so every position pool must have a non-empty replacement tier.
        assert len(hitter_pools["1B"].replacement_players) > 0
        # Phase 4b
        # Iterate UTIL pool with composite RLP baseline
        # Use per-position mode to avoid clobbering tier attributes of players
        # who remain in their original position pools
        print("  Iterating UTIL pool with composite RLP baseline...")
        util_pool = iterate_to_convergence_per_position(
            {"UTIL": util_pool_phase4a},
            budget_config,
            league_settings,
        )["UTIL"]

        # Add UTIL to hitter pools
        hitter_pools["UTIL"] = util_pool

        assert hitter_pools is not None
        for pos, pool in hitter_pools.items():
            assert pool is not None
            if pos == "OF":
                assert len(pool.rostered_players) == num_teams * 3
            else:
                assert len(pool.rostered_players) == num_teams

            # assert no players are duplicated across positions
            for check_pos, check_pool in hitter_pools.items():
                if check_pos == pos:
                    continue
                for player in check_pool.rostered_players:
                    assert player not in pool.rostered_players

            # Tier composition (top-N by internal dollar-proxy rank) is
            # the key invariant; total_z is the intuitive sum and its
            # within-tier order tracks settled-z magnitude, not the rank.
            # Tier integrity. UTIL is iterated per_position so its tier flag
            # lives in valuations_by_position[pos].tier (finalize copies that
            # to top-level in Phase 4c, but this test exercises Phase 4b
            # before finalize runs). For the per-position pools, fall back
            # to the top-level tier with the primary_position guard.
            def _tier(player, pos):
                pv = player.valuation.valuations_by_position.get(pos)
                return pv.tier if pv is not None else player.valuation.tier

            if pos == "UTIL":
                for player in pool.rostered_players:
                    assert _tier(player, pos) == "ROSTERED", (
                        f"UTIL pool: {player.name} rostered but tier={_tier(player, pos)}"
                    )
                for player in pool.replacement_players:
                    assert _tier(player, pos) == "REPLACEMENT"
                for player in pool.below_replacement:
                    assert _tier(player, pos) == "BELOW_REPLACEMENT"
            else:
                for player in pool.rostered_players:
                    if player.valuation.primary_position == pos:
                        assert player.valuation.tier == "ROSTERED", f"{pos} pool: {player.name} in rostered_players but tier={player.valuation.tier}"
                for player in pool.replacement_players:
                    if player.valuation.primary_position == pos:
                        assert player.valuation.tier == "REPLACEMENT", f"{pos} pool: {player.name} in replacement_players but tier={player.valuation.tier}"
                for player in pool.below_replacement:
                    if player.valuation.primary_position == pos:
                        assert player.valuation.tier == "BELOW_REPLACEMENT", f"{pos} pool: {player.name} in below_replacement but tier={player.valuation.tier}"


class TestBudgetsPhase5:
    def test_budget_allocation(
        self,
        hitter_pools_with_util_pool_converged_phase4b: dict[str, PositionPool],
        budget_config,
        league_budget: LeagueBudget,
    ):
        """Test Phase 5 budget allocation and distribution"""
        # The budget is only assigned for hitters and pitchers above replacement (so subtract a dollar per bench slot)
        assert league_budget.total == 11 * 260 - 11 * 5
        assert league_budget.hitter_budget == league_budget.total * 0.7
        hitter_pools = hitter_pools_with_util_pool_converged_phase4b
        hitter_pools = allocate_position_budgets(
            hitter_pools, league_budget, budget_config
        )
        hitter_budget: float = 0.0
        for pool in hitter_pools.values():
            for value in pool.category_budgets.values():
                hitter_budget += value

        assert hitter_budget == pytest.approx(league_budget.hitter_budget)

        hitter_pools = calc_pool_dollars_per_z(hitter_pools)
        # Act -- add up production on a position basis; except OBP and SLG since those are pool weighted
        pool_production: dict[str, dict[str, float]] = {}
        total_production: dict[str, float] = {}
        pool_budgets: dict[str, dict[str, float]] = {}
        for pos, pool in hitter_pools.items():
            pool_production[pos] = {}
            pool_budgets[pos] = pool.category_budgets
            for player in pool.rostered_players:
                for cat, value in player.stats.model_dump().items():  # type: ignore
                    cat = cat.upper()
                    if cat in ["OBP", "SLG"]:
                        continue
                    # model_dump now includes optional Savant diagnostic fields
                    # (xwoba, sprint_speed, ...) that are None when a player has
                    # no Savant record — they aren't production stats.
                    if value is None:
                        continue
                    pool_production[pos][cat] = pool_production[pos].get(cat, 0) + value
                    total_production[cat] = total_production.get(cat, 0) + value

        # Assert -- manual check on pool prodcutions with the pool.production_share values
        for pos, stats in pool_production.items():
            for cat, value in stats.items():
                if cat in league_budget.category_budgets["hitter"].keys():
                    pool_production_pct = (
                        pool_production[pos][cat] / total_production[cat]
                    )
                    pool_budgets_pct = (
                        pool_budgets[pos][cat]
                        / league_budget.category_budgets["hitter"][cat]
                    )
                    assert pool_production_pct - pool_budgets_pct == pytest.approx(0), (
                        f"Production percentage {pool_production_pct} does not match budget percentage {pool_budgets_pct} for category {cat}"
                    )
                    assert pool_production_pct - hitter_pools[pos].production_share[
                        cat
                    ] == pytest.approx(0), (
                        f"Production percentage {pool_production_pct} does not match production share {hitter_pools[pos].production_share[cat]} for category {cat}"
                    )

        # Distribute dollars to all hitter players
        for pos, pool in hitter_pools.items():
            for player in pool.rostered_players + pool.replacement_players:
                dollar_values = distribute_player_dollars(player, pool)
                total_dollars = sum(dollar_values.values())

                # Store dollars on the player's valuation
                player.valuation.dollar_values = dollar_values
                player.valuation.total_dollars = total_dollars

            players_distribution = sum(
                p.valuation.total_dollars for p in pool.rostered_players
            )
            position_tot_budget = sum(b for b in pool.category_budgets.values())
            assert players_distribution - position_tot_budget == pytest.approx(0), (
                f"Total dollars distributed to players ({players_distribution}) does not match pool total dollars ({position_tot_budget})"
            )

    def test_per_position_dollars_sum_to_budget_phase5(
        self,
        converged_hitter_pools_deduped: tuple[dict[str, PositionPool], int],
        budget_config,
        league_settings,
        league_budget: LeagueBudget,
    ):
        """Regression: per-position dollars must sum to the pool's budget.

        The real pipeline distributes with store_per_position=True, which
        applies the $/Z rate (derived from top-level Z-scores) to the
        per-position Z-scores in valuations_by_position. iterate_to_convergence_global
        keeps those two score copies in sync, so the per-position total_dollars
        stored for the detailed exports stay budget-balanced. Before that sync,
        the per-position scores were stale from the Phase 3b pass and the totals
        drifted off budget.
        """
        import copy

        deduped, _ = converged_hitter_pools_deduped
        hitter_pools = copy.deepcopy(deduped)

        # Phase 3d: re-iterate post-dedupe, then sync valuations_by_position.
        hitter_pools = iterate_to_convergence_global(
            hitter_pools, budget_config, league_settings
        )
        sync_pool_z_to_position(hitter_pools)

        # Phase 5: allocate budgets, then distribute per-position.
        hitter_pools = allocate_position_budgets(
            hitter_pools, league_budget, budget_config
        )
        hitter_pools = calc_pool_dollars_per_z(hitter_pools)
        distribute_pool_dollars(hitter_pools, store_per_position=True)

        for pos, pool in hitter_pools.items():
            per_position_total = sum(
                p.valuation.valuations_by_position[pos].total_dollars
                for p in pool.rostered_players
            )
            pool_budget = sum(pool.category_budgets.values())
            assert per_position_total == pytest.approx(pool_budget), (
                f"{pos}: per-position dollars {per_position_total} "
                f"!= pool budget {pool_budget}"
            )


class TestBuildPitcherPoolsPhase6:
    def test_build_starters_pool_phase6a(
        self, starters, league_settings, budget_config
    ):
        """Phase 6a should return all SPs sorted by FIP"""
        print("\nPhase 6: Building pitcher pools...")
        pitcher_pool: dict[str, PositionPool] = {
            "SP": build_pitcher_pool(
                starters,
                league_settings["roster_slots"],
                league_settings["num_teams"],
                "SP",
                budget_config["replacement_tier_pct"],
                budget_config["min_replacement_tier_size"],
            )
        }
        assert pitcher_pool is not None
        sp_pool = pitcher_pool["SP"]
        assert sp_pool.roster_slots == 44
        # Assert properly sorted by FIP; ascending
        assert all(
            sp_pool.rostered_players[i].stats.fip
            <= sp_pool.rostered_players[i + 1].stats.fip
            for i in range(len(sp_pool.rostered_players) - 1)
        )

    def test_converge_sp_pool_phase6b(
        self, sp_pool_phase6a, budget_config, league_settings
    ):
        # Phase 6b
        print("  Iterating SP pool to convergence...")
        pitcher_pool = iterate_to_convergence_global(
            sp_pool_phase6a, budget_config, league_settings
        )
        assert pitcher_pool is not None
        sp_pool = pitcher_pool["SP"]
        assert sp_pool.roster_slots == 44
        assert len(sp_pool.rostered_players) == sp_pool.roster_slots
        # Tier composition is the invariant; sort within rostered is by
        # the internal dollar-proxy rank, not by total_z.
        for player in sp_pool.rostered_players:
            assert player.valuation.tier == "ROSTERED"
        for player in sp_pool.replacement_players:
            assert player.valuation.tier == "REPLACEMENT"
        for player in sp_pool.below_replacement:
            assert player.valuation.tier == "BELOW_REPLACEMENT"

    def test_build_relievers_phase6c(self, relievers, budget_config, league_settings):
        # Phase 6c
        pitcher_pool: dict[str, PositionPool] = {
            "RP": build_pitcher_pool(
                relievers,
                league_settings["roster_slots"],
                league_settings["num_teams"],
                "RP",
                budget_config["replacement_tier_pct"],
                budget_config["min_replacement_tier_size"],
            )
        }
        assert pitcher_pool is not None
        rp_pool = pitcher_pool["RP"]
        assert rp_pool.roster_slots == 33
        # Assert properly sorted by FIP; ascending
        assert all(
            rp_pool.rostered_players[i].stats.fip
            <= rp_pool.rostered_players[i + 1].stats.fip
            for i in range(len(rp_pool.rostered_players) - 1)
        )

    def test_converge_relievers_phase6d(
        self, rp_pool_phase6c, budget_config, league_settings
    ):
        # Phase 6d
        print("  Iterating RP pool to convergence...")
        pitcher_pool = iterate_to_convergence_global(
            rp_pool_phase6c, budget_config, league_settings
        )
        assert pitcher_pool is not None
        rp_pool = pitcher_pool["RP"]
        assert rp_pool.roster_slots == 33
        assert len(rp_pool.rostered_players) == rp_pool.roster_slots
        # Tier composition is the invariant; sort within rostered is by
        # the internal dollar-proxy rank, not by total_z.
        for player in rp_pool.rostered_players:
            assert player.valuation.tier == "ROSTERED"
        for player in rp_pool.replacement_players:
            assert player.valuation.tier == "REPLACEMENT"
        for player in rp_pool.below_replacement:
            assert player.valuation.tier == "BELOW_REPLACEMENT"


class TestPitcherBudgetsPhase7:
    def test_allocate_sp_budgets_phase7(
        self, converged_sp_pool, budget_config, league_budget: LeagueBudget
    ):
        print("\nPhase 7: Allocating pitcher budgets...")
        sp_pool: dict[str, PositionPool] = converged_sp_pool
        sp_pool.update(
            {
                "SP": allocate_pool_budget(
                    sp_pool["SP"],
                    league_budget.sp_budget,
                    budget_config["sp_category_weights"],
                )
            }
        )
        total_sp_budget = sum(sp_pool["SP"].category_budgets.values())
        assert total_sp_budget == league_budget.sp_budget
        sp_pool.update(calc_pool_dollars_per_z(sp_pool))
        assert sum(
            sp_pool["SP"].total_pool_z[cat] * sp_pool["SP"].dollars_per_z[cat]
            for cat in sp_pool["SP"].category_budgets.keys()
        ) == pytest.approx(total_sp_budget)

    def test_allocate_rp_budgets_phase7(
        self, converged_rp_pool, budget_config, league_budget: LeagueBudget
    ):
        print("\nPhase 7: Allocating RP budgets...")
        rp_pool: dict[str, PositionPool] = converged_rp_pool
        rp_pool.update(
            {
                "RP": allocate_pool_budget(
                    rp_pool["RP"],
                    league_budget.rp_budget,
                    budget_config["rp_category_weights"],
                )
            }
        )
        total_rp_budget = sum(rp_pool["RP"].category_budgets.values())
        assert total_rp_budget == league_budget.rp_budget
        assert rp_pool["RP"].dollars_per_z.get("IP", None) is None
        rp_pool.update(calc_pool_dollars_per_z(rp_pool))
        assert sum(
            rp_pool["RP"].total_pool_z[cat] * rp_pool["RP"].dollars_per_z[cat]
            for cat in rp_pool["RP"].category_budgets.keys()
        ) == pytest.approx(total_rp_budget)


class TestPitcherBudgetDistributionPhase8:
    def test_allocate_pitcher_budgets_phase8(
        self,
        sp_pool_with_budget_phase7,
        rp_pool_with_budget_phase7,
        league_budget: LeagueBudget,
    ):
        print("\nPhase 8: Calculating pitcher dollar values...")
        pitchers: dict[str, PositionPool] = (
            sp_pool_with_budget_phase7 | rp_pool_with_budget_phase7
        )

        for pos, pool in pitchers.items():
            allocated_dollars: float = 0.0
            for player in pool.rostered_players + pool.replacement_players:
                # Calculate dollar values for THIS position
                dollar_values = distribute_player_dollars(player, pool)
                total_dollars = sum(dollar_values.values())
                if player in pool.rostered_players:
                    allocated_dollars += total_dollars

                player.valuation.dollar_values = dollar_values
                player.valuation.total_dollars = total_dollars
                player.valuation.primary_position = pool.position

            assert allocated_dollars == pytest.approx(
                sum(p.valuation.total_dollars for p in pool.rostered_players)
            )

            if pos == "SP":
                assert pytest.approx(allocated_dollars) == league_budget.sp_budget
            if pos == "RP":
                assert pytest.approx(allocated_dollars) == league_budget.rp_budget


class TestPipelineValidationPhase9:
    def test_validate_budget_balance_phase9a(
        self,
        hitter_pools_with_budgets_phase5,
        pitchers_with_dollars_phase8,
        league_budget: LeagueBudget,
        capsys,
    ):
        all_pools = hitter_pools_with_budgets_phase5 | pitchers_with_dollars_phase8
        validate_budget_balance(all_pools, league_budget)

        # Capture and verify printed output
        captured = capsys.readouterr()
        assert "Budget Validation" in captured.out
        assert "✓ Budget balance check PASSED" in captured.out

    def test_validate_tier_counts(
        self,
        hitter_pools_with_budgets_phase5,
        pitchers_with_dollars_phase8,
        league_settings,
        capsys,
    ):
        all_pools = hitter_pools_with_budgets_phase5 | pitchers_with_dollars_phase8
        validate_tier_counts(
            all_pools, league_settings["roster_slots"], league_settings["num_teams"]
        )
        # Capture and verify printed output
        captured = capsys.readouterr()
        assert "✓ All tier counts match expected roster slots" in captured.out

    def test_validate_rlp_z_scores(
        self,
        hitter_pools_with_budgets_phase5,
        pitchers_with_dollars_phase8,
        capsys,
    ):
        all_pools = hitter_pools_with_budgets_phase5 | pitchers_with_dollars_phase8
        validate_rlp_z_scores(all_pools)

        # Capture and verify printed output
        captured = capsys.readouterr()
        assert "✓ All RLP Z-scores are near 0" in captured.out

    def test_validate_position_valuation_hydration_warnings(self, capsys):
        """Test validation warnings for missing/empty position valuations."""
        from mtbl_valuations.domain.models import (
            HitterStats,
            Player,
            PositionPool,
            PositionValuation,
        )

        # Create test players with various issues (12 players to test truncation)
        players = []
        for i in range(12):
            p = Player(
                id=str(i),
                name=f"Player {i}",
                team="T",
                positions=["SS"],
                role="HITTER",
                stats=HitterStats(pa=10, ab=10, r=5, hr=1, rbi=1, sbn=0, obp=0.3, slg=0.4),
            )
            # Half missing position valuation, half with empty dollar_values
            if i % 2 == 0:
                # Missing position valuation
                pass
            else:
                # Empty dollar values
                p.valuation.valuations_by_position["SS"] = PositionValuation(
                    position="SS",
                    normalized_z={},
                    total_z=0.0,
                    tier="ROSTERED",
                    position_rank=0,
                    dollar_values={},
                )
            players.append(p)

        pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
        pool.rostered_players = players
        pool.replacement_players = []
        pool.below_replacement = []

        validate_position_valuation_hydration({"SS": pool})

        captured = capsys.readouterr()
        assert "⚠️  PositionValuation Hydration Warnings:" in captured.out
        assert "Player 0" in captured.out
        assert "... and 2 more" in captured.out  # 12 warnings, showing first 10
