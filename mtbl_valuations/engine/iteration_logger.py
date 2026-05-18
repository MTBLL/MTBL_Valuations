"""Per-iteration tabular logging of the valuation pipeline.

Writes one log file per (source, phase, position, iteration) into a
timestamped run directory. Two levels of detail:

    INSIGHTS — players + tiers + total_z + RLP archetype one-liner
    DEBUG    — same plus raw / z per category, stdev, baseline shift, dollar
               tables

Each source also gets a ``{source}_summary.log`` at the run-dir top level
with a manifest, convergence index, and all auto-flagged warnings.

This module lives under ``engine/`` rather than ``utils/`` because it reads
engine-specific shapes (PositionPool, PositionValuation) and calls
``get_player_stat`` from the valuation module.
"""

from __future__ import annotations

import hashlib
import logging
from contextvars import ContextVar, Token
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..domain.models import Player, PositionPool
from .valuation import get_player_stat

# Custom log level between INFO (20) and WARNING (30).
INSIGHTS = 25
logging.addLevelName(INSIGHTS, "INSIGHTS")

_LEVEL_NAMES = {"INSIGHTS": INSIGHTS, "DEBUG": logging.DEBUG}

# Auto-warning thresholds
_Z_OUTLIER = 4.0
_BASELINE_SHIFT_LIMIT = 1.0

# Phases that get per-iteration log files written. Anything else (pitcher
# phases, or contexts where no phase was pushed) is a no-op. Phase 5 budget
# logging is driven by an explicit pipeline call (not via this list).
_LOGGED_ITER_PHASES = frozenset(
    {"phase3b-iter", "phase3d-reiter", "phase4b-util"}
)


def parse_iter_log_level(name: str | None) -> int | None:
    """Return the numeric level for an iteration-log level name, or None."""
    if name is None:
        return None
    return _LEVEL_NAMES[name.upper()]


class IterationLogger:
    """Per-source logger covering iteration and budget phases."""

    def __init__(self, run_dir: Path, source: str, level: int) -> None:
        self.run_dir = run_dir
        self.source = source
        self.level = level
        self.warnings: list[dict[str, Any]] = []
        self.convergence: list[dict[str, Any]] = []
        # Last-iteration state per (phase, pos) for delta detection
        self._prev_hash: dict[tuple[str, str], str] = {}
        self._prev_rostered_ids: dict[tuple[str, str], set[str]] = {}

    # ----- helpers ----------------------------------------------------

    def _pos_path(self, pos: str) -> Path:
        """Single log file per (source, pos). All phases / iterations are
        appended to it with section banners so the file is grep-friendly:

            grep -n "^PHASE:" logs/.../updated/SS.log

        lists every section start with its line number.
        """
        d = self.run_dir / self.source
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{pos}.log"

    @staticmethod
    def _banner(phase: str, pos: str, source: str, iteration: int | None) -> str:
        bar = "=" * 80
        head = (
            f"PHASE: {phase}   |   POS: {pos}   |   SOURCE: {source}"
            if iteration is None
            else f"PHASE: {phase}   |   POS: {pos}   |   ITER: {iteration}   |   SOURCE: {source}"
        )
        return f"\n\n{bar}\n{head}\n{bar}\n"

    @staticmethod
    def _composition_hash(pool: PositionPool) -> str:
        ids = sorted(p.id for p in pool.rostered_players)
        return hashlib.sha1(",".join(ids).encode()).hexdigest()[:10]

    def _z_total(self, player: Player, pos: str, per_position: bool) -> float:
        if per_position and pos in player.valuation.valuations_by_position:
            return player.valuation.valuations_by_position[pos].total_z
        return player.valuation.total_z

    def _z_by_cat(
        self, player: Player, pos: str, per_position: bool
    ) -> dict[str, float]:
        if per_position and pos in player.valuation.valuations_by_position:
            return player.valuation.valuations_by_position[pos].normalized_z
        return player.valuation.normalized_z

    def _dollars(
        self, player: Player, pos: str, per_position: bool
    ) -> tuple[dict[str, float], float]:
        if per_position and pos in player.valuation.valuations_by_position:
            pv = player.valuation.valuations_by_position[pos]
            return pv.dollar_values, pv.total_dollars
        return player.valuation.dollar_values, player.valuation.total_dollars

    # ----- iteration snapshot -----------------------------------------

    def log_iter(
        self,
        pool: PositionPool,
        phase: str,
        iteration: int,
        per_position: bool,
        categories: list[str],
    ) -> None:
        """Emit one iteration's snapshot for a single pool."""
        if phase not in _LOGGED_ITER_PHASES:
            return
        pos = pool.position
        key = (phase, pos)
        comp_hash = self._composition_hash(pool)
        prev_hash = self._prev_hash.get(key)
        prev_ids = self._prev_rostered_ids.get(key, set())
        cur_ids = {p.id for p in pool.rostered_players}
        promoted = cur_ids - prev_ids
        demoted = prev_ids - cur_ids
        changed = comp_hash != prev_hash

        out = self._pos_path(pos)
        with open(out, "a") as f:
            f.write(self._banner(phase, pos, self.source, iteration))
            f.write(f"ts: {datetime.now().isoformat(timespec='seconds')}\n")
            n_total = len(
                pool.rostered_players
                + pool.replacement_players
                + pool.below_replacement
            )
            f.write(
                f"pool_size: {n_total}  "
                f"rostered: {len(pool.rostered_players)}  "
                f"replacement: {len(pool.replacement_players)}  "
                f"below: {len(pool.below_replacement)}\n"
            )
            f.write(f"composition_hash: {comp_hash}")
            if prev_hash is not None:
                tag = "SAME" if not changed else "DIFFERENT"
                f.write(
                    f"  (vs iter {iteration - 1}: {tag} — "
                    f"promoted={len(promoted)} demoted={len(demoted)})"
                )
            f.write("\n\n")

            # RLP / scale block
            self._write_rlp_block(f, pool, categories)

            # Player rows
            rows = []
            for p in pool.rostered_players:
                rows.append(self._player_row(p, pos, per_position, "ROSTERED", categories))
            for p in pool.replacement_players:
                rows.append(self._player_row(p, pos, per_position, "REPLACEMENT", categories))
            rows.sort(key=lambda r: -r["total_z"])
            for i, r in enumerate(rows):
                r["rank"] = i + 1
            if rows:
                df = pd.DataFrame(rows)
                expected = ["rank", "name", "tier", "total_z"]
                cols = [c for c in expected if c in df.columns] + [
                    c for c in df.columns if c not in expected
                ]
                df = df[cols]
                f.write("rostered + replacement:\n")
                f.write(
                    df.to_string(index=False, float_format=lambda x: f"{x:.3f}")
                )
                f.write("\n\n")
            f.write(f"below_replacement: {len(pool.below_replacement)} (truncated)\n")

            # Tier-move delta
            if prev_hash is not None and changed:
                f.write("\ntier moves vs prev iter:\n")
                cur_rostered_by_id = {p.id: p for p in pool.rostered_players}
                cur_other_by_id = {
                    p.id: p
                    for p in pool.replacement_players + pool.below_replacement
                }
                for pid in sorted(promoted):
                    cand: Player | None = cur_rostered_by_id.get(pid)
                    name = cand.name if cand else pid
                    f.write(f"  + {name}: → ROSTERED\n")
                for pid in sorted(demoted):
                    cand = cur_other_by_id.get(pid)
                    name = cand.name if cand else pid
                    f.write(f"  - {name}: ROSTERED → out\n")

            # z-outlier warnings
            outliers = []
            for p in pool.rostered_players:
                z = self._z_total(p, pos, per_position)
                if abs(z) > _Z_OUTLIER:
                    outliers.append((p.name, z))
            if outliers:
                f.write("\nwarnings:\n")
                for name, z in outliers:
                    f.write(f"  z outlier: {name} total_z={z:.2f}\n")
                    self.warnings.append(
                        {
                            "source": self.source,
                            "phase": phase,
                            "pos": pos,
                            "iter": iteration,
                            "kind": "z_outlier",
                            "msg": f"{name} total_z={z:.2f}",
                        }
                    )

        self._prev_hash[key] = comp_hash
        self._prev_rostered_ids[key] = cur_ids

    def _write_rlp_block(
        self, f: Any, pool: PositionPool, categories: list[str]
    ) -> None:
        if self.level <= logging.DEBUG:
            cols: dict[str, list[Any]] = {"cat": categories}
            cols["rlp_raw_avg"] = [pool.rlp_raw_avg.get(c, 0.0) for c in categories]
            cols["rostered_stdev"] = [
                pool.rostered_tier_stdevs.get(c, 0.0) for c in categories
            ]
            if pool.rlp_archetype:
                cols["archetype_raw"] = [
                    pool.rlp_archetype.get(c, 0.0) for c in categories
                ]
            if pool.rlp_z_baseline:
                cols["rlp_z_baseline"] = [
                    pool.rlp_z_baseline.get(c, 0.0) for c in categories
                ]
            arch = pd.DataFrame(cols)
            f.write("RLP / scale:\n")
            f.write(arch.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
            f.write("\n\n")
        else:
            arch_str = "  ".join(
                f"{c}={pool.rlp_raw_avg.get(c, 0.0):.3f}" for c in categories
            )
            f.write(f"rlp_raw_avg: {arch_str}\n\n")

    def _player_row(
        self,
        p: Player,
        pos: str,
        per_position: bool,
        tier_label: str,
        categories: list[str],
    ) -> dict[str, Any]:
        z_by_cat = self._z_by_cat(p, pos, per_position)
        row: dict[str, Any] = {
            "name": p.name,
            "tier": tier_label,
            "total_z": self._z_total(p, pos, per_position),
        }
        if self.level <= logging.DEBUG:
            for c in categories:
                row[f"{c}_raw"] = get_player_stat(p, c)
                row[f"{c}_z"] = z_by_cat.get(c, 0.0)
        return row

    # ----- convergence outcome ----------------------------------------

    def log_converged(
        self, phase: str, pos: str, iters_run: int, converged: bool, max_iters: int
    ) -> None:
        if phase not in _LOGGED_ITER_PHASES:
            return
        self.convergence.append(
            {
                "source": self.source,
                "phase": phase,
                "pos": pos,
                "iters_run": iters_run,
                "converged": converged,
            }
        )
        if not converged:
            self.warnings.append(
                {
                    "source": self.source,
                    "phase": phase,
                    "pos": pos,
                    "iter": max_iters,
                    "kind": "max_iter_reached",
                    "msg": f"did not converge after {max_iters} iterations",
                }
            )

    # ----- budget snapshot --------------------------------------------

    def log_budget(
        self,
        pool: PositionPool,
        phase: str,
        per_position: bool,
        categories: list[str],
    ) -> None:
        """Emit a Phase-5-style budget snapshot for one pool."""
        pos = pool.position
        out = self._pos_path(pos)
        with open(out, "a") as f:
            f.write(self._banner(phase, pos, self.source, None))
            f.write(f"ts: {datetime.now().isoformat(timespec='seconds')}\n\n")

            cat_df = pd.DataFrame(
                {
                    "cat": categories,
                    "budget": [
                        pool.category_budgets.get(c, 0.0) for c in categories
                    ],
                    "$/Z": [pool.dollars_per_z.get(c, 0.0) for c in categories],
                    "total_pool_z": [
                        pool.total_pool_z.get(c, 0.0) for c in categories
                    ],
                    "production_share": [
                        pool.production_share.get(c, 0.0) for c in categories
                    ],
                    "z_baseline_shift": [
                        pool.z_baseline_shift.get(c, 0.0) for c in categories
                    ],
                }
            )
            f.write("category budgets:\n")
            f.write(cat_df.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
            f.write("\n\n")

            # Warnings on the budget shape
            for c in categories:
                shift = pool.z_baseline_shift.get(c, 0.0)
                if shift > _BASELINE_SHIFT_LIMIT:
                    self.warnings.append(
                        {
                            "source": self.source,
                            "phase": phase,
                            "pos": pos,
                            "iter": 0,
                            "kind": "baseline_shift",
                            "msg": f"{c} z_baseline_shift={shift:.2f}",
                        }
                    )
                dpz = pool.dollars_per_z.get(c, 0.0)
                if dpz < 0:
                    self.warnings.append(
                        {
                            "source": self.source,
                            "phase": phase,
                            "pos": pos,
                            "iter": 0,
                            "kind": "negative_dollars_per_z",
                            "msg": f"{c} $/Z={dpz:.3f}",
                        }
                    )

            # Player dollar rows
            rows = []
            for p in pool.rostered_players + pool.replacement_players:
                dv, td = self._dollars(p, pos, per_position)
                tier = "ROSTERED" if p in pool.rostered_players else "REPLACEMENT"
                if tier == "ROSTERED" and td <= 0:
                    self.warnings.append(
                        {
                            "source": self.source,
                            "phase": phase,
                            "pos": pos,
                            "iter": 0,
                            "kind": "nonpositive_rostered_dollars",
                            "msg": f"{p.name} total_$={td:.2f}",
                        }
                    )
                z = self._z_total(p, pos, per_position)
                row: dict[str, Any] = {
                    "name": p.name,
                    "tier": tier,
                    "total_z": z,
                    "total_$": td,
                }
                if self.level <= logging.DEBUG:
                    for c in categories:
                        row[f"{c}_$"] = dv.get(c, 0.0)
                rows.append(row)
            rows.sort(key=lambda r: -r["total_$"])
            if rows:
                df = pd.DataFrame(rows)
                f.write("players (rostered + replacement, by $):\n")
                f.write(
                    df.to_string(index=False, float_format=lambda x: f"{x:.3f}")
                )
                f.write("\n")

    # ----- summary ----------------------------------------------------

    def finalize_summary(self) -> None:
        """Write the per-source summary file (manifest + convergence + warnings)."""
        out = self.run_dir / f"{self.source}_summary.log"
        with open(out, "w") as f:
            f.write(f"=== source: {self.source} ===\n")
            f.write(f"ts: {datetime.now().isoformat(timespec='seconds')}\n\n")
            if self.convergence:
                f.write("CONVERGENCE\n-----------\n")
                conv = pd.DataFrame(self.convergence)
                f.write(conv.to_string(index=False))
                f.write("\n\n")
            f.write(f"WARNINGS ({len(self.warnings)})\n")
            f.write("-" * (10 + len(str(len(self.warnings)))) + "\n")
            if self.warnings:
                w = pd.DataFrame(self.warnings)
                f.write(w.to_string(index=False))
                f.write("\n")
            else:
                f.write("none\n")


# ---- ContextVar plumbing -------------------------------------------
# Keeps iteration / budget code free of explicit logger parameters.

_logger_var: ContextVar[IterationLogger | None] = ContextVar(
    "iter_logger", default=None
)
_phase_var: ContextVar[str] = ContextVar("iter_phase", default="iter")


def current_logger() -> IterationLogger | None:
    return _logger_var.get()


def current_phase() -> str:
    return _phase_var.get()


def push_logger(logger: IterationLogger | None) -> Token:
    return _logger_var.set(logger)


def pop_logger(token: Token) -> None:
    _logger_var.reset(token)


def push_phase(phase: str) -> Token:
    return _phase_var.set(phase)


def pop_phase(token: Token) -> None:
    _phase_var.reset(token)
