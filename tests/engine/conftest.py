import pytest

from mtbl_valuations.domain import PositionPool
from mtbl_valuations.engine.pools import build_position_pools


@pytest.fixture(scope="session")
def dh_and_regular_hitters(players_from_hitters) -> tuple[list, list]:
    """Returns the values from Phase 3a"""
    # Identify pure DH players (only eligible for DH/UTIL, no pitcher eligibility)
    pure_dh_players = [
        h
        for h in players_from_hitters
        if set(h.positions).issubset({"DH", "UTIL"}) or h.name == "Shohei Ohtani"
    ]

    # Regular hitters (not pure DH)
    regular_hitters = [h for h in players_from_hitters if h not in pure_dh_players]

    return pure_dh_players, regular_hitters


@pytest.fixture(scope="session")
def regular_hitter_pools(
    dh_and_regular_hitters, league_settings, budget_config
) -> dict[str, PositionPool]:
    """Returns the values from Phase 3a"""
    _, regular_hitters = dh_and_regular_hitters

    return build_position_pools(
        regular_hitters,
        league_settings["roster_slots"],
        league_settings["num_teams"],
        "HITTER",
        budget_config["replacement_tier_pct"],
        budget_config["min_replacement_tier_size"],
        use_eligibility=True,  # Players appear in ALL eligible positions
    )


@pytest.fixture(scope="session")
def starters(pitchers):
    return [pp.player for pp in pitchers if pp.player.role == "SP"]


@pytest.fixture(scope="session")
def relievers(pitchers):
    return [pp.player for pp in pitchers if pp.player.role == "RP"]
