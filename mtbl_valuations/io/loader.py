"""Data loading and normalization for TRP valuation system."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.models import (
    HitterPlayer,
    HitterStats,
    PitcherPlayer,
    PitcherStats,
    Player,
    Valuation,
)


def load_batters(file_path: Path) -> list[HitterPlayer]:
    """Load and normalize batter data from batters_matched.json."""
    with open(file_path) as f:
        data = json.load(f)

    hitter_players: list[HitterPlayer] = []

    for record in data:
        # Skip if no statistics or projections
        assert "stats" in record and "projections" in record["stats"]

        proj = record["stats"]["projections"]

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

    return hitter_players


def load_pitchers(file_path: Path) -> list[PitcherPlayer]:
    """Load and normalize pitcher data from pitchers_matched.json."""
    with open(file_path) as f:
        data = json.load(f)

    pitcher_players: list[PitcherPlayer] = []

    for record in data:
        # Skip if no projections
        assert "stats" in record and "projections" in record["stats"]

        proj = record["stats"]["projections"]

        # Determine role from primary_position
        primary_pos = record.get("primary_position", "")
        assert primary_pos in ["SP", "RP"]

        # Ensure required projection fields exist
        assert all(key in proj for key in ["GS", "IP", "ERA", "WHIP", "K/9", "FIP"])
        # Use IP projection to override RP classification for swingmen
        # RPs with >100 IP projection are likely swingmen/long relievers who should be SP
        projected_gs = float(proj.get("GS", 0))
        svhd = proj.get("SVHD", proj.get("SV", 0) + proj.get("HLD", 0))
        if primary_pos == "RP" and projected_gs > svhd:
            role = "SP"
        else:
            role = primary_pos

        # Convert IP to outs
        outs = proj.get("OUTS", float(proj["IP"]) * 3)

        stats = PitcherStats(
            outs=outs,
            era=proj.get("ERA"),
            whip=proj.get("WHIP"),
            k9=proj.get("K/9"),
            qs=proj.get("QS", 0),
            svhd=svhd,
            fip=proj.get("FIP"),
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
    roster_slots: dict[str, int] = {}
    for slot_id, count in data["roster_settings"]["lineup_slot_counts"].items():
        slot_name = SLOT_MAPPING.get(int(slot_id))
        if slot_name and count > 0:
            roster_slots[slot_name] = count

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
