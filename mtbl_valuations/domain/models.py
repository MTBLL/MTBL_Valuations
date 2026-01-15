"""Core data structures for TRP valuation system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union, TYPE_CHECKING

if TYPE_CHECKING:
    StatsType = Union["HitterStats", "PitcherStats"]

Role = Literal["HITTER", "SP", "RP"]
Tier = Literal["ROSTERED", "REPLACEMENT", "BELOW_REPLACEMENT"]


@dataclass
class HitterStats:
    """Hitter stat payload."""

    pa: float
    ab: float
    r: float
    hr: float
    rbi: float
    sbn: float  # Net SB (SB - CS)
    obp: float
    slg: float
    wrc_plus: float = 0.0  # Optional: used for sorting/diagnostics


@dataclass
class PitcherStats:
    """Pitcher stat payload."""

    outs: float  # Preferred representation; IP = outs / 3
    era: float
    whip: float
    k9: float
    qs: float = 0.0  # SP only
    svhd: float = 0.0  # RP only
    fip: float = 0.0  # Optional: used for sorting/diagnostics


@dataclass
class PositionValuation:
    """Valuation for a specific position context."""

    position: str
    raw_z: dict[str, float]
    normalized_z: dict[str, float]
    dollar_values: dict[str, float]
    total_z: float
    total_dollars: float
    tier: Tier


@dataclass
class ComputedValues:
    """Computed valuation fields."""

    primary_position: str = ""
    raw_z: dict[str, float] = field(default_factory=dict)
    normalized_z: dict[str, float] = field(default_factory=dict)
    total_z: float = 0.0
    dollar_values: dict[str, float] = field(default_factory=dict)
    total_dollars: float = 0.0
    tier: Tier = "BELOW_REPLACEMENT"
    valuations_by_position: dict[str, "PositionValuation"] = field(default_factory=dict)


@dataclass
class Player:
    """Shared identity + computed fields."""

    id: str
    name: str
    team: str
    positions: list[str]
    role: Role
    stats: Union[HitterStats, PitcherStats, None] = None
    computed: ComputedValues = field(default_factory=ComputedValues)


@dataclass
class HitterPlayer:
    """Player + HitterStats."""

    player: Player
    stats: HitterStats


@dataclass
class PitcherPlayer:
    """Player + PitcherStats."""

    player: Player
    stats: PitcherStats


@dataclass
class PositionPool:
    """Position pool with tiers and budget allocation."""

    position: str
    role: Role
    roster_slots: int
    rostered_players: list[Player] = field(default_factory=list)
    replacement_players: list[Player] = field(default_factory=list)
    below_replacement: list[Player] = field(default_factory=list)
    rostered_tier_means: dict[str, float] = field(default_factory=dict)
    rostered_tier_stdevs: dict[str, float] = field(default_factory=dict)
    rlp_archetype: dict[str, float] = field(default_factory=dict)
    rlp_raw_z_avg: dict[str, float] = field(default_factory=dict)
    category_budgets: dict[str, float] = field(default_factory=dict)
    dollars_per_z: dict[str, float] = field(default_factory=dict)
    total_pool_z: dict[str, float] = field(default_factory=dict)
    production_share: dict[str, float] = field(default_factory=dict)
    weighted_pa: float = 0.0


@dataclass
class LeagueBudget:
    """League-wide budget structure."""

    total: float
    hitter_budget: float
    pitcher_budget: float
    sp_budget: float
    rp_budget: float
    category_budgets: dict[str, dict[str, float]] = field(
        default_factory=lambda: {"hitter": {}, "sp": {}, "rp": {}}
    )
