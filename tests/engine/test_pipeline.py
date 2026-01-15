from pathlib import Path

from mtbl_valuations.engine.pipeline import run_trp_valuation


def test_pipeline(batters_file, pitchers_file, league_file, budget_config_file):
    """Test that the full pipeline runs without errors."""
    output_dir = Path(".temp/")
    output_dir.mkdir(exist_ok=True)

    # Should complete without raising exceptions
    run_trp_valuation(
        batters_file, pitchers_file, league_file, budget_config_file, output_dir
    )

    # Verify output files were created
    assert (output_dir / "valuations.csv").exists()
    assert (output_dir / "position_summary.csv").exists()
    assert (output_dir / "hitters.json").exists()
    assert (output_dir / "pitchers.json").exists()
