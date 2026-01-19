"""Core data structures for TRP valuation system."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    StatsType = Union["HitterStats", "PitcherStats"]

Role = Literal["HITTER", "SP", "RP"]
Tier = Literal["ROSTERED", "REPLACEMENT", "BELOW_REPLACEMENT"]


class MTBLBaseModel(BaseModel):
    """Base model with shared config for domain types."""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class HitterStats(MTBLBaseModel):
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


class PitcherStats(MTBLBaseModel):
    """Pitcher stat payload."""

    outs: float  # Preferred representation; IP = outs / 3
    era: float
    whip: float
    k9: float
    qs: float = 0.0  # SP only
    svhd: float = 0.0  # RP only
    fip: float = 0.0  # Optional: used for sorting/diagnostics


class PositionValuation(MTBLBaseModel):
    """
    Valuation for a specific position context.
    Position Valuation does not have applied dollars since the player will have a single position by the time valuation is computed.
    """

    position: str
    normalized_z: dict[str, float]
    total_z: float
    tier: Tier
    position_rank: int


class Valuation(MTBLBaseModel):
    """Computed valuation fields."""

    primary_position: str = ""
    normalized_z: dict[str, float] = Field(default_factory=dict)
    total_z: float = 0.0
    dollar_values: dict[str, float] = Field(default_factory=dict)
    total_dollars: float = 0.0
    tier: Tier = "REPLACEMENT"
    valuations_by_position: dict[str, PositionValuation] = Field(default_factory=dict)


class Player(MTBLBaseModel):
    """Shared identity + computed fields."""

    id: str
    name: str
    team: str
    positions: list[str]
    primary_position: str = ""  # Set during position assignment, defaults to empty
    role: Role
    stats: Union[HitterStats, PitcherStats, None] = None
    valuation: Valuation = Field(default_factory=Valuation)


class HitterPlayer(MTBLBaseModel):
    """Player + HitterStats."""

    player: Player
    stats: HitterStats


class PitcherPlayer(MTBLBaseModel):
    """Player + PitcherStats."""

    player: Player
    stats: PitcherStats


class PositionPool(MTBLBaseModel):
    """Position pool with tiers and budget allocation."""

    position: str
    role: Role
    roster_slots: int
    rostered_players: list[Player] = Field(default_factory=list)
    replacement_players: list[Player] = Field(default_factory=list)
    below_replacement: list[Player] = Field(default_factory=list)
    rostered_tier_stdevs: dict[str, float] = Field(default_factory=dict)
    rlp_archetype: dict[str, float] = Field(default_factory=dict)
    rlp_raw_avg: dict[str, float] = Field(default_factory=dict)
    rlp_z_baseline: dict[str, float] = Field(default_factory=dict)
    category_budgets: dict[str, float] = Field(default_factory=dict)
    dollars_per_z: dict[str, float] = Field(default_factory=dict)
    total_pool_z: dict[str, float] = Field(default_factory=dict)
    production_share: dict[str, float] = Field(default_factory=dict)
    z_baseline_shift: dict[str, float] = Field(
        default_factory=dict
    )  # Baseline shift per category for negative Z handling


class LeagueBudget(MTBLBaseModel):
    """League-wide budget structure."""

    total: float
    hitter_budget: float
    pitcher_budget: float
    sp_budget: float
    rp_budget: float
    category_budgets: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {"hitter": {}, "sp": {}, "rp": {}}
    )
