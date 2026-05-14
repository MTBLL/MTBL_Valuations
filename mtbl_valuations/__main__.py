from __future__ import annotations

import sys
from pathlib import Path

import click

from mtbl_valuations.config import LOAD_OUTPUT_DIR, TRANSFORM_OUTPUT_DIR
from mtbl_valuations.engine.pipeline import run_all_valuations
from mtbl_valuations.utils.log import configure_logging


@click.group()
def cli() -> None:
    """MTBL Extract-Transform orchestrator and TRP valuation engine."""
    pass


@cli.command()
@click.option(
    "--batters-file",
    type=click.Path(exists=True, path_type=Path),
    default=TRANSFORM_OUTPUT_DIR / "batters_matched.json",
    show_default=True,
    help="Path to batters_matched.json",
)
@click.option(
    "--pitchers-file",
    type=click.Path(exists=True, path_type=Path),
    default=TRANSFORM_OUTPUT_DIR / "pitchers_matched.json",
    show_default=True,
    help="Path to pitchers_matched.json",
)
@click.option(
    "--league-file",
    type=click.Path(exists=True, path_type=Path),
    default=TRANSFORM_OUTPUT_DIR / "league_10998_summary.json",
    show_default=True,
    help="Path to league summary JSON",
)
@click.option(
    "--budget-config",
    type=click.Path(exists=True, path_type=Path),
    default=Path("budget_config.json"),
    show_default=True,
    help="Path to budget configuration JSON",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=LOAD_OUTPUT_DIR,
    show_default=True,
    help="Output directory for valuation results",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase log verbosity. Default: warnings only. "
    "-v: INFO (progress, skip counts). -vv: DEBUG (per-record detail, "
    "e.g. each player skipped for missing projections).",
)
@click.option(
    "--log-level",
    type=click.Choice(["WARNING", "INFO", "DEBUG"], case_sensitive=False),
    default=None,
    help="Set log level explicitly. Overrides -v/--verbose when given.",
)
def hydrate(
    batters_file: Path,
    pitchers_file: Path,
    league_file: Path,
    budget_config: Path,
    output_dir: Path,
    verbose: int,
    log_level: str | None,
) -> None:
    """Run TRP (True Replacement Price) valuation engine.

    Processes player projections and calculates market-calibrated dollar values
    using Z-scores and replacement level baselines. Valuations are produced for
    all three Fangraphs projection sources (preseason, updated, ros): per-source
    CSVs land in subdirectories of the output dir, with a single merged
    hitters.json / pitchers.json holding every source.
    """
    configure_logging(verbosity=verbose, log_level=log_level)
    try:
        run_all_valuations(
            batters_file,
            pitchers_file,
            league_file,
            budget_config,
            output_dir,
        )
    except KeyboardInterrupt:
        print("\n\n✗ Process interrupted by user\n", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"\n✗ Unexpected error: {exc}\n", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


def main() -> None:
    """Entry point for CLI."""
    cli()


if __name__ == "__main__":
    main()
