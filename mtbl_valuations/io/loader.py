"""Data loading and normalization for TRP valuation system."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

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

# Upstream nests three Fangraphs projection sets under stats.fangraphs:
#   projections    - preseason projection (full season)
#   projs_updated  - in-season updated full-season projection
#   ros            - rest-of-season projection (only published for active
#                    MLB-universe players; null for minors/NRI/FA/IL-60)
ProjectionSource = Literal["projections", "projs_updated", "ros"]

# A valuation source is a raw Fangraphs projection set, the "synthetic"
# source derived from Statcast data (see io/synthetic.py), or the "current"
# source built from current-season actuals (see io/current.py).
ValuationSource = Literal[
    "projections", "projs_updated", "ros", "synthetic", "current"
]


def load_batters(
    file_path: Path, source: ProjectionSource = "projections"
) -> list[HitterPlayer]:
    """Load and normalize batter data from batters_matched.json.

    Args:
        file_path: Path to batters_matched.json
        source: Which Fangraphs projection set to value against. See
            ProjectionSource. Players with no projection for the chosen source
            are skipped (e.g. most players have no ``ros`` line).
    """
    with open(file_path) as f:
        data = json.load(f)

    hitter_players: list[HitterPlayer] = []
    skipped_no_projections = 0

    for record in data:
        # Upstream restructured stats: Fangraphs projections now live under
        # stats.fangraphs.{projections,projs_updated,ros}
        assert "stats" in record and "fangraphs" in record["stats"]

        proj = record["stats"]["fangraphs"].get(source)

        # Skip players with no projection for this source (prospects, inactive
        # roster, or — for ros — anyone outside the active MLB universe).
        if not proj:
            skipped_no_projections += 1
            logger.debug(
                "No Fangraphs %s for batter %s (id_espn=%s) — skipping",
                source,
                record.get("name", "<unknown>"),
                record.get("id_espn", "<unknown>"),
            )
            continue

        # Ensure required projection fields exist
        assert all(
            key in proj
            for key in [
                "PA",
                "AB",
                "R",
                "HR",
                "RBI",
                "SB",
                "CS",
                "OBP",
                "SLG",
                "wRC+",
                "wOBA",
            ]
        )

        # Calculate SBN (Net SB = SB - CS)
        sbn = proj.get("SBN", proj.get("SB", 0) - proj.get("CS", 0))

        # Savant diagnostics — observed Statcast data, absent for ~70% of
        # players. Sub-sections default to {} so missing data yields None.
        savant = record["stats"].get("savant") or {}
        savant_all = savant.get("all") or {}
        savant_hr = savant.get("home_runs") or {}
        savant_speed = savant.get("sprint_speed") or {}

        stats = HitterStats(
            pa=float(proj["PA"]),
            ab=float(proj["AB"]),
            r=float(proj["R"]),
            hr=float(proj["HR"]),
            rbi=float(proj["RBI"]),
            sbn=float(sbn),
            obp=float(proj["OBP"]),
            slg=float(proj["SLG"]),
            wrc_plus=float(proj.get("wRC+", 100.0)),
            woba=float(proj.get("wOBA", 0.0)),
            wraa=float(proj.get("wRAA", 0.0)),
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

    if skipped_no_projections:
        logger.info(
            "Skipped %d batters with no Fangraphs %s", skipped_no_projections, source
        )

    return hitter_players


def load_pitchers(
    file_path: Path, source: ProjectionSource = "projections"
) -> list[PitcherPlayer]:
    """Load and normalize pitcher data from pitchers_matched.json.

    Args:
        file_path: Path to pitchers_matched.json
        source: Which Fangraphs projection set to value against. See
            ProjectionSource. Players with no projection for the chosen source
            are skipped (e.g. most players have no ``ros`` line).
    """
    with open(file_path) as f:
        data = json.load(f)

    pitcher_players: list[PitcherPlayer] = []
    skipped_no_projections = 0

    for record in data:
        # Upstream restructured stats: Fangraphs projections now live under
        # stats.fangraphs.{projections,projs_updated,ros}
        assert "stats" in record and "fangraphs" in record["stats"]

        proj = record["stats"]["fangraphs"].get(source)

        # Skip players with no projection for this source (prospects, inactive
        # roster, or — for ros — anyone outside the active MLB universe).
        if not proj:
            skipped_no_projections += 1
            logger.debug(
                "No Fangraphs %s for pitcher %s (id_espn=%s) — skipping",
                source,
                record.get("name", "<unknown>"),
                record.get("id_espn", "<unknown>"),
            )
            continue

        # Determine role from primary_position
        primary_pos = record.get("primary_position", "")
        assert primary_pos in ["SP", "RP"]

        # Ensure required projection fields exist
        assert all(key in proj for key in ["GS", "IP", "ERA", "WHIP", "K/9", "FIP"])
        # Use IP projection to override RP classification for swingmen
        # RPs with >100 IP projection are likely swingmen/long relievers who should be SP
        projected_gs = float(proj.get("GS", 0))
        svhd = proj.get("SVHD", proj.get("SV", 0) + proj.get("HLD", 0))
        role: Literal["SP", "RP"]
        if primary_pos == "RP" and projected_gs > svhd:
            role = "SP"
        else:
            role = primary_pos

        # Convert IP to outs
        outs = proj.get("OUTS", float(proj["IP"]) * 3)

        # Savant diagnostics — observed Statcast data, absent for ~70% of
        # players. Sub-section defaults to {} so missing data yields None.
        savant = record["stats"].get("savant") or {}
        savant_exp = savant.get("expected_statistics") or {}

        stats = PitcherStats(
            outs=outs,
            era=proj.get("ERA"),
            whip=proj.get("WHIP"),
            k9=proj.get("K/9"),
            qs=proj.get("QS", 0),
            svhd=svhd,
            fip=proj.get("FIP"),
            xera=savant_exp.get("xERA"),
            xwoba=savant_exp.get("xwOBA"),
        )

        player = Player(
            id=str(record["id_espn"]),
            name=record["name"],
            team=record["pro_team"],
            positions=record["eligible_slots"],
            primary_position=record.get("primary_position", ""),
            role=role,
            stats=stats,
            valuation=Valuation(),
        )

        pitcher_players.append(PitcherPlayer(player=player, stats=stats))

    if skipped_no_projections:
        logger.info(
            "Skipped %d pitchers with no Fangraphs %s", skipped_no_projections, source
        )

    return pitcher_players


def load_league_settings(file_path: Path) -> dict[str, Any]:
    """Load league settings from league_summary.json."""
    with open(file_path) as f:
        data = json.load(f)

    # ESPN slot ID mappings
    SLOT_MAPPING = {
        0: "C",
        1: "1B",
        2: "2B",
        3: "3B",
        4: "SS",
        5: "OF",
        12: "UTIL",
        13: "P",
        14: "SP",
        15: "RP",
        16: "BENCH",
        17: "IL",
    }

    # Convert lineup_slot_counts to position names
    # My league requires 4 GS to have a valid pitching category counts, so we split the P slots into SP and RP
    # TODO: import the league settings minimums from upstream so we can programmatically figure out roster minimums
    roster_slots: dict[str, int] = {}
    for slot_id, count in data["roster_settings"]["lineup_slot_counts"].items():
        slot_name = SLOT_MAPPING.get(int(slot_id))
        if slot_name and count > 0:
            roster_slots[slot_name] = roster_slots.get(slot_name, 0) + count
        if slot_name == "P" and count > 0:
            split_p_slots = count // 2
            roster_slots["SP"] = roster_slots.get("SP", 0) + split_p_slots
            roster_slots["RP"] = roster_slots.get("RP", 0) + split_p_slots

    # Extract scoring categories
    batting_categories = [cat["name"] for cat in data["scoring_categories"]["batting"]]
    pitching_categories = [
        cat["name"] for cat in data["scoring_categories"]["pitching"]
    ]
    reverse_categories = [
        cat["name"]
        for cat in data["scoring_categories"]["batting"]
        + data["scoring_categories"]["pitching"]
        if cat.get("is_reverse", False)
    ]

    return {
        "num_teams": data["num_teams"],
        "roster_slots": roster_slots,
        "auction_budget": data["draft_auction_budget"],
        "acquisition_budget": data["acquisition_budget"],
        "batting_categories": batting_categories,
        "pitching_categories": pitching_categories,
        "reverse_categories": reverse_categories,
    }


def load_budget_config(file_path: Path) -> dict[str, Any]:
    """Load budget configuration."""
    with open(file_path) as f:
        return json.load(f)
