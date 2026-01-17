from typing import TYPE_CHECKING

from mtbl_valuations.engine.valuation import get_categories

if TYPE_CHECKING:
    from mtbl_valuations.domain.models import PositionPool


class TestIteration:
    def test_iteration_to_convergence(self, converged_hitter_pools):
        """
        Test convergence results using cached fixture.

        Uses converged_hitter_pools fixture which caches the expensive
        iterate_to_convergence() operation across test runs.
        """
        # Use cached convergence results
        results: dict[str, PositionPool] = converged_hitter_pools

        # Act
        last_rostered_ss = results["SS"].rostered_players[-1]
        first_rlp_ss = results["SS"].replacement_players[0]

        # Assert
        assert results is not None
        assert len(results["SS"].rostered_players) == 11
        assert len(results["OF"].rostered_players) == 33
        assert (
            last_rostered_ss.valuation.valuations_by_position["SS"].total_z
            > first_rlp_ss.valuation.valuations_by_position["SS"].total_z
        )

    def test_rlp_raw_avg_and_replacement_present(
        self, converged_hitter_pools, league_settings
    ):
        """
        Validate RLP stats and replacement tier exist before budget allocation.

        Uses converged_hitter_pools fixture which caches the expensive
        iterate_to_convergence() operation across test runs.
        """
        # Use cached convergence results
        results: dict[str, PositionPool] = converged_hitter_pools

        ss_pool = results["SS"]

        # Replacement tier should exist and be non-empty
        assert len(ss_pool.replacement_players) > 0

        # RLP raw averages should be populated for all hitter categories
        expected_categories = set(get_categories(ss_pool.role, league_settings))
        assert expected_categories, "Expected hitter categories from league settings"
        assert set(ss_pool.rlp_raw_avg.keys()) == expected_categories
