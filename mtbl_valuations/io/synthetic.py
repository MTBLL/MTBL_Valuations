"""Synthesize HitterStats / PitcherStats from observed Statcast (Savant) data.

Builds an artificial projection purely from Savant signals, on the same
counting/rate scale as the Fangraphs projections, so it flows through the
unchanged 12-phase valuation pipeline as a 4th "synthetic" source.

Synthesis is two-pass: pass 1 derives population baselines (sprint-speed
distribution, league xwOBA, league run rate, league whiff%), pass 2 builds
each player's synthetic stat line against those baselines. A player is
skipped if they have no Savant record or no scaffold projection.

Hitter categories
-----------------
- OBP / SLG : Savant xOBP / xSLG directly (already on-scale rates)
- HR        : Savant xHR rate-converted to projected playing time
- SBN       : projected SBN modulated by sprint-speed percentile
- R / RBI   : blended expected run creation (xwOBA / wRC+ / swing_take.runs_all)
              applied as a quality ratio against the projection's R / RBI

Pitcher categories
------------------
- ERA       : Savant xERA directly (already on ERA scale)
- WHIP      : league-average WHIP scaled by xwOBA-against vs league
- K/9       : league-average K/9 scaled by whiff% vs league
- IP/QS/SVHD: from the projection (no Statcast proxy for role/usage)

The WHIP and K/9 bridges scale a league baseline (not the pitcher's own
projection) by a skill ratio — scaling the projection would double-count
skill it already reflects (an elite-whiff reliever blowing up to 36 K/9).
"""

from __future__ import annotations

import json
from bisect import bisect_left
from pathlib import Path
from typing import Any, Literal

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

# Code-side defaults so the pipeline runs even if budget_config has no
# "synthetic" block. Every value is overridable from budget_config.json.
SYNTHETIC_DEFAULTS: dict[str, Any] = {
    "scaffold_source": "projs_updated",
    "woba_scale": 1.25,
    "sbn_speed_alpha": 0.4,
    "run_blend_weights": {"xwoba": 0.6, "wrc_plus": 0.25, "swing_take": 0.15},
}

# The synthetic source is gated by the same sliding "qualified" threshold as
# the current-season source (see io/qualified.py). Tiny-sample players (e.g.
# an 18-PA call-up with a fluke .700 xSLG) would otherwise be valued as
# full-season talents AND pollute the population baselines used for z-scores.

# Fallback league baselines used only if pass 1 finds no population data.
_FALLBACK_LG_XWOBA_HITTER = 0.320
_FALLBACK_LG_R_PER_PA = 0.12
_FALLBACK_LG_XWOBA_PITCHER = 0.310
_FALLBACK_LG_WHIFF = 25.0
_FALLBACK_LG_K9 = 8.5
_FALLBACK_LG_WHIP = 1.25


def _synthetic_config(config: dict[str, Any]) -> dict[str, Any]:
    """Merge the budget_config 'synthetic' block over the code-side defaults."""
    merged = dict(SYNTHETIC_DEFAULTS)
    merged.update(config.get("synthetic") or {})
    weights = dict(SYNTHETIC_DEFAULTS["run_blend_weights"])
    weights.update(merged.get("run_blend_weights") or {})
    merged["run_blend_weights"] = weights
    return merged


def _percentile_rank(sorted_values: list[float], value: float) -> float:
    """Fractional rank (0-1) of value within an ascending sorted population."""
    if not sorted_values:
        return 0.5
    return bisect_left(sorted_values, value) / len(sorted_values)


def _blend(terms: list[tuple[float | None, float]]) -> float:
    """Weighted blend of (value, weight) pairs, renormalizing over the terms
    that are actually present — a missing signal redistributes its weight
    rather than dragging the result toward zero."""
    present = [(v, w) for v, w in terms if v is not None and w > 0]
    total_w = sum(w for _, w in present)
    if total_w <= 0:
        return 0.0
    return sum(v * w for v, w in present) / total_w


def load_batters_synthetic(
    file_path: Path, config: dict[str, Any], qualified_pa: float
) -> list[HitterPlayer]:
    """Build synthetic HitterPlayers from Statcast data.

    Args:
        file_path: Path to batters_matched.json
        config: Full budget config dict; the "synthetic" block (if any)
            overrides the code-side defaults.
        qualified_pa: Sliding minimum Statcast PA for a player to be
            synthesized (see io/qualified.compute_qualified_pa).
    """
    syn = _synthetic_config(config)
    scaffold = syn["scaffold_source"]
    woba_scale = float(syn["woba_scale"])
    sbn_alpha = float(syn["sbn_speed_alpha"])
    min_pa = qualified_pa
    rbw = syn["run_blend_weights"]

    with open(file_path) as f:
        data = json.load(f)

    # ---- Pass 1: population baselines ----
    # Only players who clear the Statcast-sample gate inform the baselines, so
    # the valued population and the baseline population are the same set.
    sprint_speeds: list[float] = []
    xwoba_sum = 0.0
    xwoba_pa = 0.0
    r_sum = 0.0
    pa_sum = 0.0
    for record in data:
        fg = record.get("stats", {}).get("fangraphs") or {}
        proj = fg.get(scaffold)
        savant = record.get("stats", {}).get("savant") or {}
        savant_all = savant.get("all") or {}
        speed = savant.get("sprint_speed") or {}

        savant_pa = savant_all.get("PA")
        if not proj or not savant_pa or float(savant_pa) < min_pa:
            continue
        if speed.get("sprint_speed") is not None:
            sprint_speeds.append(float(speed["sprint_speed"]))
        xw = savant_all.get("xwOBA")
        if xw is not None:
            xwoba_sum += float(xw) * float(savant_pa)
            xwoba_pa += float(savant_pa)
        if proj.get("R") is not None and proj.get("PA"):
            r_sum += float(proj["R"])
            pa_sum += float(proj["PA"])

    sprint_speeds.sort()
    lg_xwoba = xwoba_sum / xwoba_pa if xwoba_pa else _FALLBACK_LG_XWOBA_HITTER
    lg_r_per_pa = r_sum / pa_sum if pa_sum else _FALLBACK_LG_R_PER_PA

    # ---- Pass 2: synthesize ----
    hitter_players: list[HitterPlayer] = []
    skipped = 0
    for record in data:
        fg = record.get("stats", {}).get("fangraphs") or {}
        proj = fg.get(scaffold)
        savant = record.get("stats", {}).get("savant") or {}
        savant_all = savant.get("all") or {}

        # Require a scaffold projection (playing time), core Savant rate data
        # (the skill signal), and a Statcast sample large enough to be
        # signal rather than noise; otherwise skip this player.
        xobp = savant_all.get("xOBP")
        xslg = savant_all.get("xSLG")
        xwoba = savant_all.get("xwOBA")
        savant_pa = savant_all.get("PA")
        if not proj or xobp is None or xslg is None or xwoba is None:
            skipped += 1
            continue
        if not savant_pa or float(savant_pa) < min_pa:
            skipped += 1
            continue
        proj_pa = float(proj.get("PA", 0.0))
        if proj_pa <= 0:
            skipped += 1
            continue

        # --- HR: rate-convert Savant xHR to projected playing time ---
        savant_hr = savant.get("home_runs") or {}
        xhr = savant_hr.get("xHR")
        if xhr is not None and savant_pa:
            hr_syn = float(xhr) / float(savant_pa) * proj_pa
        else:
            hr_syn = float(proj.get("HR", 0.0))  # fall back to projection

        # --- SBN: projected SBN modulated by sprint-speed percentile ---
        speed = savant.get("sprint_speed") or {}
        sprint = speed.get("sprint_speed")
        proj_sbn = float(proj.get("SBN", proj.get("SB", 0) - proj.get("CS", 0)))
        if sprint is not None and sprint_speeds:
            pct = _percentile_rank(sprint_speeds, float(sprint))
            sbn_syn = proj_sbn * (1.0 + sbn_alpha * (pct - 0.5))
        else:
            sbn_syn = proj_sbn

        # --- R / RBI: blended expected run creation, applied as a quality
        # ratio against the projection's own R / RBI (preserves lineup
        # context — leadoff vs cleanup keep their R-vs-RBI lean) ---
        xwrc_from_xwoba = (
            (float(xwoba) - lg_xwoba) / woba_scale + lg_r_per_pa
        ) * proj_pa
        wrc_plus = proj.get("wRC+")
        xwrc_from_wrcplus = (
            (float(wrc_plus) / 100.0) * lg_r_per_pa * proj_pa
            if wrc_plus is not None
            else None
        )
        swing_take = savant.get("swing_take") or {}
        runs_all = swing_take.get("runs_all")
        st_pa = swing_take.get("PA")
        xwrc_from_swingtake = (
            (float(runs_all) / float(st_pa) + lg_r_per_pa) * proj_pa
            if runs_all is not None and st_pa
            else None
        )
        xwrc_blended = _blend(
            [
                (xwrc_from_xwoba, float(rbw.get("xwoba", 0.0))),
                (xwrc_from_wrcplus, float(rbw.get("wrc_plus", 0.0))),
                (xwrc_from_swingtake, float(rbw.get("swing_take", 0.0))),
            ]
        )
        proj_wrc = float(proj.get("wRC", 0.0))
        quality_ratio = xwrc_blended / proj_wrc if proj_wrc > 0 else 1.0
        r_syn = float(proj.get("R", 0.0)) * quality_ratio
        rbi_syn = float(proj.get("RBI", 0.0)) * quality_ratio

        stats = HitterStats(
            pa=proj_pa,
            ab=float(proj.get("AB", proj_pa)),
            r=r_syn,
            hr=hr_syn,
            rbi=rbi_syn,
            sbn=sbn_syn,
            obp=float(xobp),
            slg=float(xslg),
            wrc_plus=float(proj.get("wRC+", 100.0)),
            woba=float(proj.get("wOBA", 0.0)),
            wraa=float(proj.get("wRAA", 0.0)),
            xwoba=savant_all.get("xwOBA"),
            xobp=savant_all.get("xOBP"),
            xslg=savant_all.get("xSLG"),
            xhr=savant_hr.get("xHR"),
            sprint_speed=speed.get("sprint_speed"),
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
            "Skipped %d batters with no Savant/scaffold data for synthetic source",
            skipped,
        )
    return hitter_players


def load_pitchers_synthetic(
    file_path: Path, config: dict[str, Any], qualified_pa: float
) -> list[PitcherPlayer]:
    """Build synthetic PitcherPlayers from Statcast data.

    Args:
        file_path: Path to pitchers_matched.json
        config: Full budget config dict; the "synthetic" block (if any)
            overrides the code-side defaults.
        qualified_pa: Sliding minimum Statcast batters-faced for a player to
            be synthesized (see io/qualified.compute_qualified_pa).
    """
    syn = _synthetic_config(config)
    scaffold = syn["scaffold_source"]
    min_pa = qualified_pa

    with open(file_path) as f:
        data = json.load(f)

    # ---- Pass 1: league baselines for the ratio bridges ----
    # Gated on Statcast sample so the baselines aren't skewed by tiny samples.
    # lg_k9 / lg_whip are league-average *projected* rates: the WHIP and K/9
    # bridges scale these league baselines by a skill ratio rather than the
    # player's own projection, which would double-count skill the projection
    # already reflects.
    xwoba_vals: list[float] = []
    whiff_vals: list[float] = []
    k9_vals: list[float] = []
    whip_vals: list[float] = []
    for record in data:
        fg = record.get("stats", {}).get("fangraphs") or {}
        proj = fg.get(scaffold)
        savant = record.get("stats", {}).get("savant") or {}
        exp = savant.get("expected_statistics") or {}
        savant_all = savant.get("all") or {}
        savant_pa = exp.get("PA")
        if not savant_pa or float(savant_pa) < min_pa:
            continue
        if exp.get("xwOBA") is not None:
            xwoba_vals.append(float(exp["xwOBA"]))
        if savant_all.get("swing_miss_pct") is not None:
            whiff_vals.append(float(savant_all["swing_miss_pct"]))
        if proj:
            if proj.get("K/9") is not None:
                k9_vals.append(float(proj["K/9"]))
            if proj.get("WHIP") is not None:
                whip_vals.append(float(proj["WHIP"]))

    lg_xwoba = (
        sum(xwoba_vals) / len(xwoba_vals)
        if xwoba_vals
        else _FALLBACK_LG_XWOBA_PITCHER
    )
    lg_whiff = (
        sum(whiff_vals) / len(whiff_vals) if whiff_vals else _FALLBACK_LG_WHIFF
    )
    lg_k9 = sum(k9_vals) / len(k9_vals) if k9_vals else _FALLBACK_LG_K9
    lg_whip = sum(whip_vals) / len(whip_vals) if whip_vals else _FALLBACK_LG_WHIP

    # ---- Pass 2: synthesize ----
    pitcher_players: list[PitcherPlayer] = []
    skipped = 0
    for record in data:
        fg = record.get("stats", {}).get("fangraphs") or {}
        proj = fg.get(scaffold)
        savant = record.get("stats", {}).get("savant") or {}
        exp = savant.get("expected_statistics") or {}
        savant_all = savant.get("all") or {}

        # Require a scaffold projection, Savant xERA (the core skill signal),
        # and a Statcast sample large enough to be signal rather than noise.
        xera = exp.get("xERA")
        savant_pa = exp.get("PA")
        primary_pos = record.get("primary_position", "")
        if not proj or xera is None or primary_pos not in ("SP", "RP"):
            skipped += 1
            continue
        if not savant_pa or float(savant_pa) < min_pa:
            skipped += 1
            continue

        # IP / role / save-hold scaffold come straight from the projection —
        # no Statcast proxy exists for pure role/usage categories.
        outs = proj.get("OUTS", float(proj.get("IP", 0.0)) * 3)
        projected_gs = float(proj.get("GS", 0))
        svhd = proj.get("SVHD", proj.get("SV", 0) + proj.get("HLD", 0))
        role: Literal["SP", "RP"]
        if primary_pos == "RP" and projected_gs > svhd:
            role = "SP"
        else:
            role = primary_pos

        # ERA: Savant xERA directly (already on ERA scale).
        era_syn = float(xera)
        # WHIP: scale the league-average WHIP by this pitcher's xwOBA-against
        # vs league. Scaling the *league* baseline (not the player's own
        # projection) avoids double-counting skill the projection already
        # reflects.
        xwoba_against = exp.get("xwOBA")
        if xwoba_against is not None and lg_xwoba > 0:
            whip_syn = lg_whip * (float(xwoba_against) / lg_xwoba)
        else:
            whip_syn = lg_whip
        # K/9: scale the league-average K/9 by this pitcher's whiff% vs league.
        whiff = savant_all.get("swing_miss_pct")
        if whiff is not None and lg_whiff > 0:
            k9_syn = lg_k9 * (float(whiff) / lg_whiff)
        else:
            k9_syn = lg_k9

        stats = PitcherStats(
            outs=outs,
            era=era_syn,
            whip=whip_syn,
            k9=k9_syn,
            qs=proj.get("QS", 0),
            svhd=svhd,
            fip=proj.get("FIP", 0.0),
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
            "Skipped %d pitchers with no Savant/scaffold data for synthetic source",
            skipped,
        )
    return pitcher_players
