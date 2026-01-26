"""Test budget allocation and valuation calculations."""

from __future__ import annotations

import pandas as pd
import pytest


class TestBudgetCalculation:
    """Test league budget calculation."""

    def test_budget_total_matches_expected(self, league_file, league_budget):
        """Test that total budget matches (num_teams * ($260 - bench_reserve))."""
        import json

        with open(league_file) as f:
            league_summary = json.load(f)

        num_teams = league_summary["num_teams"]
        expected_total = num_teams * 255

        assert league_budget.total == expected_total, (
            f"Total budget should be ${expected_total}, got ${league_budget.total}"
        )

    def test_hitter_pitcher_split(self, league_budget):
        """Test that hitter/pitcher budget split is 70/30."""
        hitter_pct = league_budget.hitter_budget / league_budget.total
        pitcher_pct = league_budget.pitcher_budget / league_budget.total

        assert abs(hitter_pct - 0.70) < 0.01, (
            f"Hitter budget should be ~70%, got {hitter_pct:.2%}"
        )
        assert abs(pitcher_pct - 0.30) < 0.01, (
            f"Pitcher budget should be ~30%, got {pitcher_pct:.2%}"
        )

    def test_budget_splits_sum_to_total(self, league_budget):
        """Test that hitter + pitcher budgets should sum to total."""
        assert (
            abs(
                (league_budget.hitter_budget + league_budget.pitcher_budget)
                - league_budget.total
            )
            < 0.01
        ), "Hitter + pitcher budgets should sum to total budget"


class TestRosteredTierBudget:
    """Test that only rostered tier players consume budget."""

    def test_only_rostered_tier_consumes_budget(self, run_trp, league_budget):
        """Test that total allocated dollars only come from rostered tier."""
        # Load position summary
        position_summary = pd.read_csv(run_trp / "position_summary.csv")

        # Load detailed CSVs for each position
        total_allocated = 0.0
        seen_players = set()  # Track players by (id, primary_position) to avoid double-counting

        for _, row in position_summary.iterrows():
            position: str = str(row["position"])
            role = row["role"]

            # Build expected filename
            if role == "HITTER":
                filename = f"{position.lower()}_detailed.csv"
            elif role == "SP":
                filename = "sp_detailed.csv"
            elif role == "RP":
                filename = "rp_detailed.csv"
            else:
                continue

            detailed_file = run_trp / filename
            if not detailed_file.exists():
                continue

            # Load detailed CSV
            df = pd.read_csv(detailed_file)

            # Filter to rostered tier only
            rostered = df[df["tier"] == "ROSTERED"]

            # Sum dollars, but only count each player once (by their primary position)
            for _, player_row in rostered.iterrows():
                player_key = (player_row["id"], player_row["primary_position"])
                if player_key not in seen_players:
                    seen_players.add(player_key)
                    total_allocated += player_row["total_dollars"]

        # Check against budget
        # Note: Allow up to $5 difference due to rounding in budget allocation
        # and per-category dollar distribution
        difference = abs(total_allocated - league_budget.total)
        assert difference < 5.0, (
            f"Total allocated from rostered tier (${total_allocated:.2f}) "
            f"should match budget (${league_budget.total:.2f}), "
            f"difference: ${difference:.2f}"
        )


class TestPositionPoolBudgets:
    """Test position pool budget allocation."""

    @pytest.mark.parametrize(
        ("role_filter", "expected_budget_attr", "label"),
        [
            ("HITTER", "hitter_budget", "hitter"),
            (["SP", "RP"], "pitcher_budget", "pitcher"),
        ],
    )
    def test_position_budgets_sum_to_budget(
        self, run_trp, league_budget, role_filter, expected_budget_attr, label
    ):
        """Test that position budgets sum to the expected total."""
        position_summary = pd.read_csv(run_trp / "position_summary.csv")

        if isinstance(role_filter, list):
            pools = position_summary[position_summary["role"].isin(role_filter)]
        else:
            pools = position_summary[position_summary["role"] == role_filter]

        total_position_budgets = pools["total_budget"].sum()
        expected_budget = getattr(league_budget, expected_budget_attr)

        # Should match expected budget (within tolerance)
        difference = abs(total_position_budgets - expected_budget)
        assert difference < 1.0, (
            f"Sum of {label} position budgets (${total_position_budgets:.2f}) "
            f"should match {label} budget (${expected_budget:.2f}), "
            f"difference: ${difference:.2f}"
        )


class TestCategoryBudgetAllocation:
    """Test position-category budget allocation matches production share."""

    def test_position_category_budgets_match_production_share(
        self, hitter_pools_with_util_pool_converged_phase4b, league_budget
    ):
        """
        Test that each position-category budget equals production share × total category budget.

        For counting stats (R, HR, RBI, SBN): share based on actual production
        For rate stats (OBP, SLG): share based on weighted PA
        """
        from mtbl_valuations.engine.budget import allocate_position_budgets

        # Allocate budgets to pools
        pools_with_budgets = allocate_position_budgets(
            hitter_pools_with_util_pool_converged_phase4b,
            league_budget,
            {
                "pa_weights": {"C": 500, "default": 600},
            },
        )

        # Get total category budgets for hitters
        hitter_category_budgets = league_budget.category_budgets["hitter"]

        # Track position-category contributions for verification
        position_cat_contributions: dict[str, dict[str, float]] = {}

        # Sum up all position-category budgets for each category
        for position, pool in pools_with_budgets.items():
            position_cat_contributions[position] = {}

            for category, budget in pool.category_budgets.items():
                position_cat_contributions[position][category] = budget

                # Verify budget matches: production_share × total_category_budget
                expected_budget = (
                    pool.production_share[category] * hitter_category_budgets[category]
                )

                assert abs(budget - expected_budget) < 0.01, (
                    f"{position} {category}: budget ${budget:.2f} should equal "
                    f"production_share ({pool.production_share[category]:.4f}) × "
                    f"total_budget (${hitter_category_budgets[category]:.2f}) = "
                    f"${expected_budget:.2f}"
                )

        # Verify that all position budgets for each category sum to the total category budget
        for category in hitter_category_budgets.keys():
            total_allocated = sum(
                position_cat_contributions[pos].get(category, 0.0)
                for pos in position_cat_contributions
            )
            expected_total = hitter_category_budgets[category]

            assert abs(total_allocated - expected_total) < 0.01, (
                f"{category}: sum of position budgets (${total_allocated:.2f}) "
                f"should equal total category budget (${expected_total:.2f})"
            )


class TestDollarsPerZ:
    """Test $/Z rate calculations."""

    def test_dollars_per_z_is_positive(self, run_trp):
        """Test that all $/Z rates are positive."""
        position_summary = pd.read_csv(run_trp / "position_summary.csv")

        # Check all columns that start with "dollars_per_z_"
        dollars_per_z_cols = [
            col for col in position_summary.columns if col.startswith("dollars_per_z_")
        ]

        for col in dollars_per_z_cols:
            # Skip NaN values
            values = position_summary[col].dropna()

            for value in values:
                assert value > 0, f"{col} should be positive, got {value:.3f}"

    def test_util_dollars_per_z_similar_to_other_positions(self, run_trp):
        """Test that UTIL $/Z rates are similar to other positions (not 3-5x higher)."""
        position_summary = pd.read_csv(run_trp / "position_summary.csv")

        # Get hitter pools
        hitter_pools = position_summary[position_summary["role"] == "HITTER"]

        # Get UTIL pool
        util_row = hitter_pools[hitter_pools["position"] == "UTIL"]
        assert len(util_row) == 1, "UTIL pool should exist"
        util_row = util_row.iloc[0]


        # Get other hitter positions
        other_hitters = hitter_pools[hitter_pools["position"] != "UTIL"]

        # Check each category
        dollars_per_z_cols = [
            col for col in util_row.index if col.startswith("dollars_per_z_")
        ]

        for col in dollars_per_z_cols:
            util_rate = util_row[col]

            if pd.isna(util_rate):
                continue

            # Get rates from other positions for this category
            other_rates = other_hitters[col].dropna()


            if len(other_rates) == 0:
                continue

            max_other_rate = other_rates.max()

            # UTIL rate should not be more than 2x the max of other positions
            assert util_rate <= max_other_rate * 2.0, (
                f"UTIL {col} ({util_rate:.3f}) should not be >2x "
                f"max other position rate ({max_other_rate:.3f})"
            )
