"""Debug tests to investigate dollar allocation issues."""

from __future__ import annotations

import pandas as pd


class TestDollarAllocationDebug:
    """Debug tests for dollar allocation."""

    def test_show_total_z_by_tier(self, run_trp):
        """Show total positive z-scores by tier to understand where budget went."""
        position_summary = pd.read_csv(run_trp / "position_summary.csv")

        print("\n=== Z-Score Distribution by Position and Tier ===\n")

        total_roster_dollars = 0.0
        total_rlp_dollars = 0.0
        total_below_dollars = 0.0

        for _, row in position_summary.iterrows():
            position = row["position"]
            role = row["role"]

            # Build filename
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

            df = pd.read_csv(detailed_file)

            # Group by tier
            rostered = df[df["tier"] == "ROSTERED"]
            rlp = df[df["tier"] == "REPLACEMENT"]
            below = df[df["tier"] == "BELOW_REPLACEMENT"]

            # Calculate stats
            roster_count = len(rostered)
            roster_total_z = rostered["total_z"].sum()
            roster_total_dollars = rostered["total_dollars"].sum()
            roster_positive_z = rostered[rostered["total_z"] > 0]["total_z"].sum()
            roster_negative_z_count = len(rostered[rostered["total_z"] < 0])

            rlp_count = len(rlp)
            rlp_total_z = rlp["total_z"].sum() if rlp_count > 0 else 0.0
            rlp_total_dollars = rlp["total_dollars"].sum() if rlp_count > 0 else 0.0

            below_count = len(below)
            below_total_z = below["total_z"].sum() if below_count > 0 else 0.0
            below_total_dollars = below["total_dollars"].sum() if below_count > 0 else 0.0

            total_roster_dollars += roster_total_dollars
            total_rlp_dollars += rlp_total_dollars
            total_below_dollars += below_total_dollars

            print(f"{position} ({role}):")
            print(f"  ROSTERED ({roster_count} players):")
            print(f"    Total z: {roster_total_z:.2f}")
            print(f"    Positive z: {roster_positive_z:.2f}")
            print(f"    Players with negative z: {roster_negative_z_count}")
            print(f"    Total dollars: ${roster_total_dollars:.2f}")
            if rlp_count > 0:
                print(f"  RLP ({rlp_count} players):")
                print(f"    Total z: {rlp_total_z:.2f}")
                print(f"    Total dollars: ${rlp_total_dollars:.2f}")
            if below_count > 0:
                print(f"  BELOW ({below_count} players):")
                print(f"    Total z: {below_total_z:.2f}")
                print(f"    Total dollars: ${below_total_dollars:.2f}")
            print()

        print(f"=== TOTALS ===")
        print(f"Rostered tier dollars: ${total_roster_dollars:.2f}")
        print(f"RLP tier dollars: ${total_rlp_dollars:.2f}")
        print(f"Below tier dollars: ${total_below_dollars:.2f}")
        print(f"GRAND TOTAL: ${total_roster_dollars + total_rlp_dollars + total_below_dollars:.2f}")

        # This test always passes - it's just for debugging
        assert True

    def test_show_negative_z_rostered_players(self, run_trp):
        """Show rostered players with negative total z-scores."""
        position_summary = pd.read_csv(run_trp / "position_summary.csv")

        print("\n=== Rostered Players with Negative Total Z ===\n")

        negative_z_players = []

        for _, row in position_summary.iterrows():
            position = row["position"]
            role = row["role"]

            # Build filename
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

            df = pd.read_csv(detailed_file)

            # Get rostered players with negative z
            rostered = df[df["tier"] == "ROSTERED"]
            negative_z = rostered[rostered["total_z"] < 0]

            for _, player in negative_z.iterrows():
                negative_z_players.append({
                    "position": position,
                    "name": player["name"],
                    "total_z": player["total_z"],
                    "total_dollars": player["total_dollars"],
                })

        # Sort by total z (most negative first)
        negative_z_players.sort(key=lambda p: p["total_z"])

        print(f"Found {len(negative_z_players)} rostered players with negative total z:\n")

        for player in negative_z_players[:20]:  # Show top 20
            print(f"  {player['position']:4} {player['name']:30} "
                  f"z={player['total_z']:6.2f} ${player['total_dollars']:6.2f}")

        # This test always passes - it's just for debugging
        assert True

    def test_check_category_budgets_vs_allocated(self, run_trp):
        """Check if category budgets match allocated dollars per category."""
        position_summary = pd.read_csv(run_trp / "position_summary.csv")

        print("\n=== Category Budget vs Allocated ===\n")

        for _, row in position_summary.iterrows():
            position = row["position"]
            role = row["role"]

            print(f"{position} ({role}):")

            # Get category budgets from position summary
            dollars_per_z_cols = [col for col in row.index if col.startswith("dollars_per_z_")]

            if not dollars_per_z_cols:
                continue

            # Load detailed CSV
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

            df = pd.read_csv(detailed_file)
            rostered = df[df["tier"] == "ROSTERED"]

            # Check each category
            for col in dollars_per_z_cols:
                category = col.replace("dollars_per_z_", "")
                dollars_per_z = row[col]

                if pd.isna(dollars_per_z):
                    continue

                # Calculate total positive z for this category from rostered tier
                z_col = f"z_{category}"
                if z_col in rostered.columns:
                    total_positive_z = rostered[rostered[z_col] > 0][z_col].sum()
                    expected_category_budget = dollars_per_z * total_positive_z

                    # Get actual allocated dollars for this category
                    dollar_col = f"dollar_{category}"
                    if dollar_col in rostered.columns:
                        actual_allocated = rostered[dollar_col].sum()

                        difference = abs(expected_category_budget - actual_allocated)

                        if difference > 1.0:
                            print(f"  {category}: expected ${expected_category_budget:.2f}, "
                                  f"actual ${actual_allocated:.2f}, diff ${difference:.2f} ✗")
                        else:
                            print(f"  {category}: ${actual_allocated:.2f} ✓")

            print()

        # This test always passes - it's just for debugging
        assert True
