"""The analysis half of tools/probe_pickup_reach.py: where a reagent's prompt stops showing."""

import sys

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tools import probe_pickup_reach as probe  # noqa: E402

_SPOT = (0.0, 0.0, 0.0)


_clock = iter(range(1, 10_000))


def _at(x: float, prompt: bool, entity: str = "Reagent_A", z: float = 0.0, t: float | None = None) -> probe.Sample:
    return probe.Sample(float(next(_clock)) if t is None else t, (x, 0.0, z), prompt, entity, _SPOT)


def test_the_summary_gives_the_farthest_prompt_and_the_nearest_without_one():
    samples = [_at(40, True), _at(110, True), _at(130, False), _at(300, False)]

    summary = probe.summarize(samples)

    assert "farthest with the prompt: 110 (3D 110)" in summary
    assert "nearest without it:       130 (3D 130)" in summary
    assert "overlap" not in summary


def test_the_edge_is_bracketed_by_where_the_prompt_came_on_and_went_off():
    """The flag lags both ways: on late walking in, off late walking out."""
    out_and_in = [_at(x, p) for x, p in [(100, True), (300, True), (340, False), (300, False), (270, True)]]
    again = [_at(x, p) for x, p in [(320, True), (310, False), (200, False), (285, True)]]

    summary = probe.summarize(out_and_in + again)

    assert "the edge: on no farther out than 285, off no nearer than 310 (2 on, 2 off)" in summary
    assert "they cross" not in summary


def test_crossings_that_disagree_are_called_out():
    summary = probe.summarize([_at(200, False), _at(330, True), _at(250, False)])

    assert "on no farther out than 330, off no nearer than 250" in summary
    assert "they cross" in summary


def test_runs_pooled_together_make_no_crossing_where_one_ends_and_the_next_begins():
    first = [_at(200, True, t=1.0), _at(350, False, t=2.0), _at(280, True, t=3.0)]
    second = [_at(400, False, t=0.5), _at(270, True, t=1.5), _at(310, False, t=2.5)]

    summary = probe.summarize(first + second)

    assert "on no farther out than 280, off no nearer than 310 (2 on, 2 off)" in summary


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
