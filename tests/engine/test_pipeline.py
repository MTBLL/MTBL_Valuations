from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mtbl_valuations.engine.budget import (
    allocate_position_budgets,
    calc_pool_dollars_per_z,
)
from mtbl_valuations.engine.iteration import iterate_to_convergence
from mtbl_valuations.engine.pipeline import run_trp_valuation
from mtbl_valuations.engine.pools import (
    build_pitcher_pool,
    build_util_pool,
    dedupe_multi_position_players,
)
from mtbl_valuations.engine.valuation import distribute_player_dollars

if TYPE_CHECKING:
    from mtbl_valuations.domain import LeagueBudget, PositionPool


class TestPipeline:
    def test_pipeline(
        self, batters_file, pitchers_file, league_file, budget_config_file
    ):
        """Test that the full pipeline runs without errors."""
        output_dir = Path(".temp/")
        output_dir.mkdir(exist_ok=True)

        # Should complete without raising exceptions
        run_trp_valuation(
            batters_file, pitchers_file, league_file, budget_config_file, output_dir
        )

        # Verify output files were created
        assert (output_dir / "valuations.csv").exists()
        assert (output_dir / "position_summary.csv").exists()
        assert (output_dir / "hitters.json").exists()
        assert (output_dir / "pitchers.json").exists()


class TestPipelinePhase3:
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
            hitter_pools = iterate_to_convergence(
                deduped,
                budget_config,
                league_settings,
                track_z_per_pool=False,  # Now single-position mode
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

                # assert players are ordered by z-score
                assert all(
                    pool.rostered_players[i].valuation.total_z
                    >= pool.rostered_players[i + 1].valuation.total_z
                    for i in range(len(pool.rostered_players) - 1)
                )

                assert len(pool.replacement_players) >= 3
            assert "UTIL" not in hitter_pools.keys()


class TestPipelinePhase4:
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
        hitter_pools = hitter_pools_deduped_converged
        # Phase 4b
        # Iterate UTIL pool with composite RLP baseline
        print("  Iterating UTIL pool with composite RLP baseline...")
        util_pool = iterate_to_convergence(
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

            # assert UTIL players are ordered by z-score
            if pos == "UTIL":
                assert all(
                    pool.rostered_players[i].valuation.total_z
                    >= pool.rostered_players[i + 1].valuation.total_z
                    for i in range(len(pool.rostered_players) - 1)
                )


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


class TestBuildPitcherPoolsPhase6:
    def test_build_starters_pool_phase6a(
        self, starters, league_settings, budget_config
    ):
        print("\nPhase 6: Building pitcher pools...")
        sp_pool: dict[str, PositionPool] = {
            "SP": build_pitcher_pool(
                starters,
                league_settings["roster_slots"],
                league_settings["num_teams"],
                "SP",
                budget_config["replacement_tier_pct"],
                budget_config["min_replacement_tier_size"],
            )
        }
        assert sp_pool is not None
