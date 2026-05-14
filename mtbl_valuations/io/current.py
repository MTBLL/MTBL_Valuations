"""Load HitterStats / PitcherStats from current-season actual production.

The 5th valuation source: it values what players have *actually* done so far
this season (``stats.espn.current_season``), gated by the sliding "qualified"
threshold (see io/qualified.py).

Stats are valued **raw** — partial-season, not pace-extrapolated. The source
is therefore not dollar-comparable to the full-season projection sources, but
z-scores ARE comparable (each source normalizes z within its own pool), which
is what surfaces sell-high / buy-low divergence from the projections: a player
whose current-source z/tier sits well above their projection-source z/tier is
outplaying expectations, and vice versa.

current_season carries no wRC+ (hitters) or FIP (pitchers) — OPS and ERA
respectively stand in as the pool-building sort seed only; they don't affect
valuations, which are driven by the league scoring categories.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from ..domain.models import (
    HitterPlayer,
    HitterStats,
    PitcherPlayer,
    PitcherStats,
    Player,
    Valuation,
)
from ..utils.log import get_logger

logger = get_logger(__name__)


def _num(value: object) -> float:
    """Coerce a current_season stat to float. ESPN current-season fields can
    be present-but-null (not just missing), so a plain dict .get default is
    not enough — None must collapse to 0.0 too."""
    return float(value) if value is not None else 0.0  # type: ignore[arg-type]


def load_batters_current(
    file_path: Path, qualified_pa: float
) -> list[HitterPlayer]:
    """Load batters valued on current-season actuals.

    Args:
        file_path: Path to batters_matched.json
        qualified_pa: Sliding minimum PA for a player to be valued
            (see io/qualified.compute_qualified_pa).
    """
    with open(file_path) as f:
        data = json.load(f)

    hitter_players: list[HitterPlayer] = []
    skipped = 0
    for record in data:
        cs = (record.get("stats", {}).get("espn", {}) or {}).get("current_season")
        if not cs or _num(cs.get("PA")) < qualified_pa:
            skipped += 1
            continue

        # Savant diagnostics are observed data — source-independent — so carry
        # them through here the same as the projection-based loaders do.
        savant = record.get("stats", {}).get("savant") or {}
        savant_all = savant.get("all") or {}
        savant_hr = savant.get("home_runs") or {}
        savant_speed = savant.get("sprint_speed") or {}

        sbn = cs.get("SBN")
        if sbn is None:
            sbn = _num(cs.get("SB")) - _num(cs.get("CS"))

        stats = HitterStats(
            pa=_num(cs.get("PA")),
            ab=_num(cs.get("AB")) or _num(cs.get("PA")),
            r=_num(cs.get("R")),
            hr=_num(cs.get("HR")),
            rbi=_num(cs.get("RBI")),
            sbn=_num(sbn),
            obp=_num(cs.get("OBP")),
            slg=_num(cs.get("SLG")),
            # No wRC+ in current_season; OPS*100 is a monotonic stand-in used
            # only as the pool-building sort seed.
            wrc_plus=_num(cs.get("OPS")) * 100.0,
            xwoba=savant_all.get("xwOBA"),
            xobp=savant_all.get("xOBP"),
            xslg=savant_all.get("xSLG"),
            xhr=savant_hr.get("xHR"),
            sprint_speed=savant_speed.get("sprint_speed"),
        )
        player = Player(
            id=str(record["id_espn"]),
            name=record["name"],
            team=record["pro_team"],
            positions=record["eligible_slots"],
            primary_position=record.get("primary_position", ""),
            role="HITTER",
            stats=stats,
            valuation=Valuation(),
        )
        hitter_players.append(HitterPlayer(player=player, stats=stats))

    if skipped:
        logger.info(
            "Skipped %d batters below the qualified threshold (%.0f PA) "
            "for current source",
            skipped,
            qualified_pa,
        )
    return hitter_players


def load_pitchers_current(
    file_path: Path, qualified_pa: float
) -> list[PitcherPlayer]:
    """Load pitchers valued on current-season actuals.

    Pitchers are gated on batters faced (TBF) — the pitcher analog of PA.

    Args:
        file_path: Path to pitchers_matched.json
        qualified_pa: Sliding minimum batters-faced for a player to be valued.
    """
    with open(file_path) as f:
        data = json.load(f)

    pitcher_players: list[PitcherPlayer] = []
    skipped = 0
    for record in data:
        cs = (record.get("stats", {}).get("espn", {}) or {}).get("current_season")
        if not cs or _num(cs.get("TBF")) < qualified_pa:
            skipped += 1
            continue

        primary_pos = record.get("primary_position", "")
        if primary_pos not in ("SP", "RP"):
            skipped += 1
            continue

        outs = cs.get("OUTS")
        if outs is None:
            outs = _num(cs.get("IP")) * 3
        gs = _num(cs.get("GS"))
        svhd = cs.get("SVHD")
        if svhd is None:
            svhd = _num(cs.get("SV")) + _num(cs.get("HLD"))
        svhd = _num(svhd)
        role: Literal["SP", "RP"]
        if primary_pos == "RP" and gs > svhd:
            role = "SP"
        else:
            role = primary_pos

        savant = record.get("stats", {}).get("savant") or {}
        exp = savant.get("expected_statistics") or {}

        stats = PitcherStats(
            outs=_num(outs),
            era=_num(cs.get("ERA")),
            whip=_num(cs.get("WHIP")),
            k9=_num(cs.get("k_per_9")),
            qs=_num(cs.get("QS")),
            svhd=svhd,
            # No FIP in current_season; ERA is a monotonic stand-in used only
            # as the pool-building sort seed.
            fip=_num(cs.get("ERA")),
            xera=exp.get("xERA"),
            xwoba=exp.get("xwOBA"),
        )
        player = Player(
            id=str(record["id_espn"]),
            name=record["name"],
            team=record["pro_team"],
            positions=record["eligible_slots"],
            primary_position=primary_pos,
            role=role,
            stats=stats,
            valuation=Valuation(),
        )
        pitcher_players.append(PitcherPlayer(player=player, stats=stats))

    if skipped:
        logger.info(
            "Skipped %d pitchers below the qualified threshold (%.0f TBF) "
            "for current source",
            skipped,
            qualified_pa,
        )
    return pitcher_players
