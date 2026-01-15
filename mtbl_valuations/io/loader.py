"""Data loading and normalization for TRP valuation system."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.models import (
    ComputedValues,
    HitterPlayer,
    HitterStats,
    PitcherPlayer,
    PitcherStats,
    Player,
)


def load_batters(file_path: Path) -> list[HitterPlayer]:
    """Load and normalize batter data from batters_matched.json."""
    with open(file_path) as f:
        data = json.load(f)

    hitter_players: list[HitterPlayer] = []

    for record in data:
        # Skip if no projections
        if "stats" not in record or "projections" not in record["stats"]:
            continue

        proj = record["stats"]["projections"]

        # Ensure required projection fields exist
        if not all(
            key in proj
            for key in ["PA", "AB", "R", "HR", "RBI", "OBP", "SLG"]
        ):
            continue

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
            role="HITTER",
            stats=stats,
            computed=ComputedValues(),
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
        if "stats" not in record or "projections" not in record["stats"]:
            continue

        proj = record["stats"]["projections"]

        # Determine role from primary_position
        primary_pos = record.get("primary_position", "")
        if primary_pos not in ["SP", "RP"]:
            continue

        # Use IP projection to override RP classification for swingmen
        # RPs with >100 IP projection are likely swingmen/long relievers who should be SP
        projected_ip = float(proj.get("IP", 0))
        if primary_pos == "RP" and projected_ip > 100:
            role = "SP"
        else:
            role = "SP" if primary_pos == "SP" else "RP"

        # Ensure required projection fields exist
        if not all(key in proj for key in ["IP", "ERA", "WHIP", "K/9"]):
            continue

        # Convert IP to outs
        outs = float(proj["IP"]) * 3

        # Calculate K/9 if not present
        k9 = float(proj.get("K/9", 0.0))

        # Calculate SVHD based on role
        if role == "RP":
            svhd = float(
                proj.get("SVHD", proj.get("SV", 0) + proj.get("HLD", 0))
            )
            qs = 0.0
        else:
            svhd = 0.0
            qs = float(proj.get("QS", 0.0))

        stats = PitcherStats(
            outs=outs,
            era=float(proj["ERA"]),
            whip=float(proj["WHIP"]),
            k9=k9,
            qs=qs,
            svhd=svhd,
            fip=float(proj.get("FIP", 0.0)),
        )

        player = Player(
            id=str(record["id_espn"]),
            name=record["name"],
            team=record["pro_team"],
            positions=record["eligible_slots"],
            role=role,
            stats=stats,
            computed=ComputedValues(),
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
    pitching_categories = [cat["name"] for cat in data["scoring_categories"]["pitching"]]
    reverse_categories = [
        cat["name"]
        for cat in data["scoring_categories"]["batting"] + data["scoring_categories"]["pitching"]
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
