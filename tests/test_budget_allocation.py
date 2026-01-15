"""Test budget allocation and valuation calculations."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.mark.skip("broken")
class TestBudgetCalculation:
    """Test league budget calculation."""

    def test_budget_total_matches_expected(self, league_file, league_budget):
        """Test that total budget matches (num_teams * ($260 - bench_reserve))."""
        import json

        with open(league_file) as f:
            league_summary = json.load(f)

        num_teams = league_summary["num_teams"]
        # Budget is (260 - 5) * 11 teams = 259 * 11 = 2849
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


@pytest.mark.skip("broken")
class TestRosteredTierBudget:
    """Test that only rostered tier players consume budget."""

    def test_only_rostered_tier_consumes_budget(self, run_trp, league_budget):
        """Test that total allocated dollars only come from rostered tier."""
        # Load position summary
        position_summary = pd.read_csv(run_trp / "position_summary.csv")

        # Load detailed CSVs for each position
        total_allocated = 0.0

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

            # Sum dollars
            total_allocated += rostered["total_dollars"].sum()

        # Check against budget
        difference = abs(total_allocated - league_budget.total)
        assert difference < 1.0, (
            f"Total allocated from rostered tier (${total_allocated:.2f}) "
            f"should match budget (${league_budget.total:.2f}), "
            f"difference: ${difference:.2f}"
        )


class TestPositionPoolBudgets:
    """Test position pool budget allocation."""

    def test_position_budgets_sum_to_hitter_budget(self, run_trp, league_budget):
        """Test that all hitter position budgets sum to total hitter budget."""
        position_summary = pd.read_csv(run_trp / "position_summary.csv")

        # Get hitter pools only
        hitter_pools = position_summary[position_summary["role"] == "HITTER"]

        total_position_budgets = hitter_pools["total_budget"].sum()

        # Should match hitter budget (within tolerance)
        difference = abs(total_position_budgets - league_budget.hitter_budget)
        assert difference < 1.0, (
            f"Sum of hitter position budgets (${total_position_budgets:.2f}) "
            f"should match hitter budget (${league_budget.hitter_budget:.2f}), "
            f"difference: ${difference:.2f}"
        )

    def test_pitcher_budgets_sum_to_pitcher_budget(self, run_trp, league_budget):
        """Test that SP + RP budgets sum to total pitcher budget."""
        position_summary = pd.read_csv(run_trp / "position_summary.csv")

        # Get pitcher pools only
        pitcher_pools = position_summary[position_summary["role"].isin(["SP", "RP"])]

        total_position_budgets = pitcher_pools["total_budget"].sum()

        # Should match pitcher budget (within tolerance)
        difference = abs(total_position_budgets - league_budget.pitcher_budget)
        assert difference < 1.0, (
            f"Sum of pitcher position budgets (${total_position_budgets:.2f}) "
            f"should match pitcher budget (${league_budget.pitcher_budget:.2f}), "
            f"difference: ${difference:.2f}"
        )


@pytest.mark.skip("broken")
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
        util_row = util_row.iloc[0]  # type: ignore

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
            other_rates = other_hitters[col].dropna()  # type: ignore

            if len(other_rates) == 0:
                continue

            max_other_rate = other_rates.max()

            # UTIL rate should not be more than 2x the max of other positions
            assert util_rate <= max_other_rate * 2.0, (
                f"UTIL {col} ({util_rate:.3f}) should not be >2x "
                f"max other position rate ({max_other_rate:.3f})"
            )
