"""Sliding "qualified player" threshold (MLB / Baseball Savant style).

A player qualifies for a leaderboard-style valuation once they've accumulated
``rate`` plate appearances per team game played. The threshold slides upward
through the season as games accumulate, so early-season call-ups aren't held
to a full-season bar in April but the bar tightens by September.

Reference points:
- MLB batting-title qualifier:        3.1 PA / team game
- Baseball Savant leaderboard bar:    2.1 PA / team game
- This project (keep impactful call-ups in the pool):  1.5 PA / team game

``team_games`` is derived empirically from the data — a high percentile of
position-player games played (the everyday regulars) — rather than from the
calendar, since a scoring-period / day counter overstates games actually
played (off-days) and traded players inflate the raw maximum.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

QUALIFIED_DEFAULTS: dict[str, float] = {
    "rate_pa_per_game": 1.5,
    "team_games_percentile": 0.80,
}


def _qualified_config(config: dict[str, Any]) -> dict[str, float]:
    """Merge the budget_config 'qualified' block over the code-side defaults."""
    merged = dict(QUALIFIED_DEFAULTS)
    merged.update(config.get("qualified") or {})
    return merged


def team_games_played(
    batters_data: list[dict[str, Any]], percentile: float
) -> int:
    """Empirical team-games-played: a high percentile of position-player games.

    Everyday regulars play ~every game, so the upper tail of the games-played
    distribution tracks the team schedule closely — without the off-day noise
    of a calendar counter or the cross-team inflation of the raw maximum.
    """
    games = sorted(
        g
        for g in (
            (
                r.get("stats", {}).get("espn", {}).get("current_season", {}) or {}
            ).get("G", 0)
            or 0
            for r in batters_data
        )
        if g
    )
    if not games:
        return 0
    idx = min(int(len(games) * percentile), len(games) - 1)
    return int(games[idx])


def compute_qualified_pa(batters_file: Path, config: dict[str, Any]) -> float:
    """Resolve the sliding qualified-PA threshold from the current data.

    Returns ``rate_pa_per_game * team_games_played``. Used to gate both the
    current-season source (on actual PA) and the synthetic source (on
    Statcast-tracked PA).
    """
    cfg = _qualified_config(config)
    with open(batters_file) as f:
        batters_data = json.load(f)
    games = team_games_played(batters_data, cfg["team_games_percentile"])
    return cfg["rate_pa_per_game"] * games


def qualified_ids(
    records: list[dict[str, Any]],
    qualified_pa: float,
    pa_field: str,
) -> set[str]:
    """``id_espn`` of every record whose ``current_season[pa_field] >= qualified_pa``.

    Mirrors the loading-time gate in ``io/current.py``
    (``load_batters_current`` checks ``PA``; ``load_pitchers_current`` checks
    ``TBF``) so any caller can rebuild the *qualified cohort* as a set of IDs
    without re-running the loaders. Pass ``"PA"`` for batters, ``"TBF"`` for
    pitchers.

    Records missing ``current_season``, ``id_espn``, or a numeric playing-time
    value are silently excluded — there's no meaningful "qualifies for what?"
    answer for them.
    """
    out: set[str] = set()
    for r in records:
        cs = (r.get("stats", {}).get("espn", {}) or {}).get("current_season")
        # Match the loader-side gate (current.py:62 / :135): a record with no
        # current_season block is excluded categorically, not coerced to 0.
        # Without this, an early-season `qualified_pa == 0` (no team games
        # played yet → team_games_played returns 0) would let missing-cs
        # records into the cohort and dilute the ranking distribution.
        if not cs:
            continue
        raw = cs.get(pa_field)
        try:
            pa = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            pa = 0.0
        if pa >= qualified_pa:
            rid = r.get("id_espn")
            if rid is not None:
                out.add(str(rid))
    return out
