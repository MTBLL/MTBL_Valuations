"""Tests for the sliding rest-of-season qualified gates
(``compute_qualified_pa_ros`` / ``compute_qualified_ip_ros``), which scale
the PA/IP bar by the games LEFT in the season rather than a flat
full-season threshold."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mtbl_valuations.io.qualified import (
    _remaining_team_games,
    compute_qualified_ip_ros,
    compute_qualified_pa_ros,
)


def _batters_file(tmp_path: Path, games: list[int]) -> Path:
    """Write a minimal batters_matched.json where each record carries an
    espn current_season G (games played)."""
    data: list[dict[str, Any]] = [
        {"id_espn": str(i), "stats": {"espn": {"current_season": {"G": g}}}}
        for i, g in enumerate(games)
    ]
    f = tmp_path / "batters.json"
    f.write_text(json.dumps(data))
    return f


def _cfg(season: int = 162, rate: float = 1.5, ip: float = 30.0) -> dict:
    return {
        "qualified": {
            "rate_pa_per_game": rate,
            "team_games_percentile": 0.80,
            "min_projection_ip": ip,
            "season_games": season,
        }
    }


def test_remaining_team_games_uses_percentile_of_games_played(tmp_path):
    # 80th percentile of [10..100 step 10] → games[8] = 90.
    data = json.loads(
        _batters_file(tmp_path, list(range(10, 101, 10))).read_text()
    )
    cfg = _cfg(season=162)["qualified"]
    assert _remaining_team_games(data, cfg) == 162 - 90


def test_ros_pa_gate_slides_with_remaining_games(tmp_path):
    # Early season (few games played) → high bar near full season.
    early = _batters_file(tmp_path, [20] * 10)
    # 80th pct of [20]*10 = 20 → remaining 142 → 1.5*142 = 213.
    assert compute_qualified_pa_ros(early, _cfg()) == 1.5 * (162 - 20)

    # Late season (many games played) → low bar.
    late = _batters_file(tmp_path, [150] * 10)
    assert compute_qualified_pa_ros(late, _cfg()) == 1.5 * (162 - 150)


def test_ros_ip_gate_scales_by_season_fraction(tmp_path):
    bf = _batters_file(tmp_path, [81] * 10)  # half season played
    # remaining 81 of 162 → 0.5 → 30 * 0.5 = 15.
    assert compute_qualified_ip_ros(bf, _cfg(ip=30)) == 15.0


def test_ros_gates_floor_at_zero_when_season_over(tmp_path):
    over = _batters_file(tmp_path, [162] * 10)
    assert compute_qualified_pa_ros(over, _cfg()) == 0.0
    assert compute_qualified_ip_ros(over, _cfg()) == 0.0
