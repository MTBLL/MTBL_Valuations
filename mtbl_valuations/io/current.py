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
    file_path: Path,
    qualified_pa: float,
    qualified_gs: float = 0.0,
) -> list[PitcherPlayer]:
    """Load pitchers valued on current-season actuals.

    Pitchers are gated on batters faced (TBF) — the pitcher analog of PA.

    Role is decided by GS vs SVHD bidirectionally so SP and RP pools
    contain comparable populations. SP and RP are essentially different
    positions and never z-compared (they live in separate pools), so the
    SVHD-vs-GS split keeps each pool clean: a primary-SP doing more
    relief than starts moves to RP; a primary-RP starting more than
    holding/saving moves to SP. A pitcher with no SVHD (e.g. a long-
    reliever who occasionally spot-starts) is treated as a starter.

    Primary-SPs whose actual GS falls below ``qualified_gs`` are skipped
    — same sliding-threshold pattern as ``qualified_pa`` for hitters
    (``rate_gs_per_team_game * team_games_played``). This catches the
    gs=0 degenerate case (no starts to per-start-normalize against) and
    filters insufficient-sample starters whose ``outs`` is dominated by
    long-relief work without enough starts to balance.

    Args:
        file_path: Path to pitchers_matched.json
        qualified_pa: Sliding minimum batters-faced for a player to be valued.
        qualified_gs: Sliding minimum games started for a primary-SP to be
            valued. Default 0 = no SP filter (used by unit tests).
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
        # Bidirectional role classification on GS vs SVHD. SP and RP are
        # different positions and never z-compared; this keeps each pool
        # filled with players actually performing that role.
        role: Literal["SP", "RP"]
        if primary_pos == "RP" and gs > svhd:
            role = "SP"
        elif primary_pos == "SP" and svhd > gs:
            role = "RP"
        else:
            role = primary_pos

        # Sliding GS gate for SPs (analog of qualified_pa). Skips gs=0
        # primary-SPs that can't be per-start-normalized at all, and
        # filters insufficient-sample starters whose ``outs`` is all
        # long-relief without enough starts to be priced as a starter.
        if role == "SP" and gs < qualified_gs:
            skipped += 1
            continue

        # Current-source SPs: normalize ``outs`` to PER-START outs
        # (``outs / gs``). Mid-season actuals have wildly varying GS counts
        # (IL stints, rotation churn), so raw IP-z conflates pitching skill
        # with opportunity. Storing per-start outs makes ``IP = outs / 3``
        # return per-start IP, so the z compares a rate (innings per start)
        # the way the rate stats (ERA / WHIP / K9) already do.
        outs_n = _num(outs)
        if role == "SP" and gs > 0:
            outs_n = outs_n / gs

        savant = record.get("stats", {}).get("savant") or {}
        exp = savant.get("expected_statistics") or {}

        stats = PitcherStats(
            outs=outs_n,
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
