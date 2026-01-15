from pathlib import Path

import click

from mtbl_valuations.utils import LOAD_OUTPUT_DIR, TRANSFORM_OUTPUT_DIR


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
def hydrate(
    batters_file: Path,
    pitchers_file: Path,
    league_file: Path,
    budget_config: Path,
    output_dir: Path,
) -> None:
    """Run TRP (True Replacement Price) valuation engine.

    Processes player projections and calculates market-calibrated dollar values
    using Z-scores and replacement level baselines.
    """
    try:
        run_trp_valuation(
            batters_file,
            pitchers_file,
            league_file,
            budget_config,
            output_dir,
        )
    except KeyboardInterrupt:
        print("\n\n✗ Process interrupted by user\n", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}\n", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


def main() -> None:
    """Entry point for CLI."""
    cli()


if __name__ == "__main__":
    main()
