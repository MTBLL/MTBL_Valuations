import pytest

from mtbl_valuations.domain import PositionPool
from mtbl_valuations.engine.pools import build_position_pools


@pytest.fixture
def regular_hitter_pools(
    players_from_hitters, league_settings, budget_config
) -> dict[str, PositionPool]:
    # Identify pure DH players (only eligible for DH/UTIL, no pitcher eligibility)
    pure_dh_players = [
        h
        for h in players_from_hitters
        if set(h.positions).issubset({"DH", "UTIL"}) or h.name == "Shohei Ohtani"
    ]

    # Regular hitters (not pure DH)
    regular_hitters = [h for h in players_from_hitters if h not in pure_dh_players]

    return build_position_pools(
        regular_hitters,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        "HITTER",
        budget_config,
        use_eligibility=True,  # Players appear in ALL eligible positions
    )
