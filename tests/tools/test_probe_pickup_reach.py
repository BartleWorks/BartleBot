"""The analysis half of tools/probe_pickup_reach.py: where a reagent's prompt stops showing."""

import sys

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tools import probe_pickup_reach as probe  # noqa: E402

_SPOT = (0.0, 0.0, 0.0)


def _at(x: float, prompt: bool, entity: str = "Reagent_A", z: float = 0.0) -> probe.Sample:
    return probe.Sample(0.0, (x, 0.0, z), prompt, entity, _SPOT)


def test_the_summary_gives_the_farthest_prompt_and_the_nearest_without_one():
    samples = [_at(40, True), _at(110, True), _at(130, False), _at(300, False)]

    summary = probe.summarize(samples)

    assert "farthest with the prompt: 110 (3D 110)" in summary
    assert "nearest without it:       130 (3D 130)" in summary
    assert "overlap" not in summary


def test_distances_are_flat_with_the_3d_beside_them():
    summary = probe.summarize([_at(30, True, z=40)])

    assert "farthest with the prompt: 30 (3D 50)" in summary


def test_an_overlap_is_called_out():
    summary = probe.summarize([_at(150, True), _at(100, False)])

    assert "the two overlap" in summary


def test_each_reagent_is_summarized_and_then_all_of_them():
    summary = probe.summarize([_at(100, True, "Reagent_A"), _at(120, True, "Reagent_B")])

    assert "Reagent_A: 1 samples" in summary and "Reagent_B: 1 samples" in summary
    assert "All reagents: 2 samples" in summary


def test_saved_samples_read_back_as_they_were_written(tmp_path):
    import csv  # noqa: PLC0415

    path = tmp_path / "run.local.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(probe.CSV_FIELDS)
        writer.writerow(["1.50", 100.0, 0.0, 0.0, 1, "Reagent_A", 0.0, 0.0, 0.0])

    (sample,) = probe.read_csvs([path])

    assert (sample.t, sample.at, sample.prompt, sample.entity, sample.flat) == (
        1.5,
        (100.0, 0.0, 0.0),
        True,
        "Reagent_A",
        100.0,
    )


def test_no_samples_says_so():
    assert probe.summarize([]) == "No samples."
