from typing import List

from mtbl_valuations.domain.models import Player
from mtbl_valuations.engine.pipeline import assign_primary_positions


def test_assign_primary_positions_hitters(
    player_from_hitters: List[Player], league_settings
):
    results = assign_primary_positions(
        player_from_hitters,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        "HITTER",
    )

    assert results is not None
