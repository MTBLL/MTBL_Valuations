from typing import TYPE_CHECKING

from mtbl_valuations.engine.iteration import iterate_to_convergence

if TYPE_CHECKING:
    from mtbl_valuations.domain.models import PositionPool


class TestIteration:
    def test_iteration_to_convergence(
        self, regular_hitter_pools, budget_config, league_settings
    ):
        """
        def iterate_to_convergence(
            pools: dict[str, PositionPool],
            budget_config: dict[str, Any],
            league_settings: dict[str, Any],
            composite_rlp_archetype: dict[str, float] | None = None,
            track_per_pool: bool = False,
        ) -> dict[str, PositionPool]:
        """
        # Arrange
        results: dict[str, PositionPool] = iterate_to_convergence(
            pools=regular_hitter_pools,
            budget_config=budget_config,
            league_settings=league_settings,
            track_z_per_pool=True,
        )
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
