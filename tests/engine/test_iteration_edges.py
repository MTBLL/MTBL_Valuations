import math

from mtbl_valuations.domain.models import HitterStats, Player, PositionPool
from mtbl_valuations.engine.iteration import _get_bucket, iterate_to_convergence


def _make_hitter(player_id: str, runs: float) -> Player:
    return Player(
        id=player_id,
        name=f"Player {player_id}",
        team="T",
        positions=["SS"],
        role="HITTER",
        stats=HitterStats(
            pa=10,
            ab=10,
            r=runs,
            hr=1,
            rbi=1,
            sbn=0,
            obp=0.3,
            slg=0.4,
        ),
    )


def test_iterate_to_convergence_composite_and_max_iterations(capsys):
    p1 = _make_hitter("1", 5)
    p2 = _make_hitter("2", 10)

    pool = PositionPool(position="SS", role="HITTER", roster_slots=1)
    pool.rostered_players = [p1, p2]
    pool.replacement_players = []
    pool.below_replacement = []

    budget_config = {
        "max_iterations": 1,
        "convergence_threshold": -1,
        "replacement_tier_pct": 0.03,
        "min_replacement_tier_size": 1,
    }
    league_settings = {"batting_categories": ["R"], "pitching_categories": []}
    composite = {"R": 7.0}

    iterate_to_convergence(
        {"SS": pool},
        budget_config,
        league_settings,
        composite_rlp_archetype=composite,
    )

    captured = capsys.readouterr()
    assert "Max iterations" in captured.out
    assert pool.rlp_raw_avg == composite


def test_get_bucket_creates_position_valuation():
    player = _make_hitter("3", 8)
    bucket = _get_bucket(player, "SS", track_per_pool=True)
    assert bucket.position == "SS"
    assert math.isfinite(bucket.total_z)
    assert _get_bucket(player, "SS", track_per_pool=False) is player.valuation
