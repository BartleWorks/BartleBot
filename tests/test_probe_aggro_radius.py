"""The analysis half of tools/probe_aggro_radius.py: pairing marks with battles, and reading a trigger."""

import csv

from tools.probe_aggro_radius import (
    GHOST_WINDOW,
    Events,
    MobPos,
    Point,
    Sample,
    Trial,
    analyse_trial,
    build_trials,
    find_run_onset,
    read_csv,
    read_csvs,
    summarise,
    write_event,
    write_sample,
)


def _walk(mob_x: list[float], player_x: float = 0.0, dt: float = 0.25, mob_z: float = 0.0) -> list[Sample]:
    """One sample per mob position, the wizard standing still at player_x."""
    return [
        Sample(i * dt, Point(player_x, 0.0, 0.0), [MobPos(7, "Mob", Point(x, 0.0, mob_z))]) for i, x in enumerate(mob_x)
    ]


def _path(points: list[tuple[float, float]], dt: float = 0.25) -> list[Sample]:
    """The wizard at each (x, y) in turn, one sample apart, with a still mob at the origin."""
    return [
        Sample(i * dt, Point(x, y, 0.0), [MobPos(7, "Mob", Point(0.0, 0.0, 0.0))]) for i, (x, y) in enumerate(points)
    ]


# Standing at (300, 0), then the forced run: three steps straight to a seat at (300, 600).
_STAND_THEN_RUN = [(300, 0)] * 6 + [(300, 200), (300, 400), (300, 600)] + [(300, 600)] * 3


def test_a_mark_without_samples_is_its_trigger_less_the_reaction():
    trials, discarded = build_trials(Events(marks=[10.0], starts=[11.5], ends=[40.0]), reaction=0.3)

    assert trials == [Trial(9.7, "hotkey", 11.5, 10.0)]
    assert discarded == []


def test_the_trigger_is_the_sample_before_the_forced_run():
    samples = _path(_STAND_THEN_RUN)

    assert find_run_onset(samples, mark=1.6, battle_start=2.75) == 1.25


def test_a_walk_that_turns_into_the_run_is_split_at_the_turn():
    # Walking along x toward the mob, then forced north to the seat.
    samples = _path([(600, 0), (550, 0), (500, 0), (450, 0), (400, 0), (400, 300), (400, 600), (400, 600)])

    assert find_run_onset(samples, mark=1.3, battle_start=1.75) == 1.0


def test_no_run_is_found_when_it_reaches_back_past_the_lookback():
    # Walking straight at the seat for the whole window: the start of the run cannot be told.
    samples = _path([(0, y) for y in range(0, 2000, 100)])

    assert find_run_onset(samples, mark=4.5, battle_start=4.75) is None


def test_no_run_is_found_when_the_wizard_never_moved():
    assert find_run_onset(_path([(300, 0)] * 8), mark=1.0, battle_start=1.75) is None


def test_a_found_run_places_the_trigger_and_a_missing_one_falls_back_to_the_mark():
    run = _path(_STAND_THEN_RUN)
    still = _path([(300, 0)] * 12)

    (found,), _ = build_trials(Events(marks=[1.6], starts=[2.75]), reaction=0.3, samples=run)
    (missing,), _ = build_trials(Events(marks=[1.6], starts=[2.75]), reaction=0.3, samples=still)

    assert found == Trial(1.25, "run", 2.75, 1.6)
    assert analyse_trial(run, found).dist2 == 300.0
    assert missing.source == "hotkey"
    assert round(missing.trigger_t, 2) == 1.3


def test_a_battle_with_no_mark_falls_back_to_in_battle():
    trials, _ = build_trials(Events(marks=[], starts=[11.5]), reaction=0.3)

    assert trials == [Trial(11.5, "in_battle", 11.5)]


def test_the_latest_mark_wins_and_the_others_are_set_aside_with_a_reason():
    events = Events(marks=[5.0, 41.0, 60.0, 70.0], starts=[10.0, 70.5], ends=[40.0])

    trials, discarded = build_trials(events, reaction=0.0)

    assert [t.trigger_t for t in trials] == [5.0, 70.0]
    assert discarded == [(41.0, "inside the ghost window after a fight"), (60.0, "no battle followed")]
    assert 41.0 < 40.0 + GHOST_WINDOW


def test_a_mark_during_a_fight_is_set_aside():
    _, discarded = build_trials(Events(marks=[25.0], starts=[10.0], ends=[40.0]), reaction=0.0)

    assert discarded == [(25.0, "pressed during a fight")]


def test_the_trigger_distance_is_3d_and_reads_an_approaching_mob():
    # The mob walks 50 units a sample toward a wizard standing still, 300 units below it.
    samples = _walk([900, 850, 800, 750, 700], mob_z=300.0)

    result = analyse_trial(samples, Trial(1.0, "hotkey", 2.0))

    assert result.dist2 == 700.0
    assert round(result.dist3, 1) == 761.6
    assert result.dz == 300.0
    assert result.approach_speed > 150
    assert result.approaching
    assert [round(dt, 2) for dt, _ in result.trace] == [-1.0, -0.75, -0.5, -0.25, 0.0]


def test_a_still_mob_is_not_approaching():
    result = analyse_trial(_walk([600] * 5), Trial(1.0, "hotkey", 2.0))

    assert result.approach_speed == 0.0
    assert not result.approaching


def test_a_trigger_before_any_sample_has_no_result():
    assert analyse_trial(_walk([600, 600]), Trial(-1.0, "hotkey", 0.0)) is None


def test_the_csv_round_trips_into_the_same_summary(tmp_path):
    samples = _walk([900, 850, 800, 750, 700])
    events = Events(marks=[1.2], starts=[2.5], ends=[20.0])
    path = tmp_path / "run.local.csv"

    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("t", "kind", "gid", "name", "x", "y", "z"))
        for s in samples:
            write_sample(writer, s)
        write_event(writer, 1.2, "mark")
        write_event(writer, 2.5, "battle_start")
        write_event(writer, 20.0, "battle_end")

    read_samples, read_events = read_csv(path)

    assert read_samples == samples
    assert read_events == events
    assert summarise(read_samples, read_events, 0.2) == summarise(samples, events, 0.2)
    assert "current radius 700 covers" not in summarise(samples, events, 0.2)


def test_pooled_runs_keep_each_battle_with_its_own_mark(tmp_path):
    paths = []
    for n in range(2):
        path = tmp_path / f"run{n}.local.csv"
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(("t", "kind", "gid", "name", "x", "y", "z"))
            for s in _walk([900, 850, 800, 750, 700]):
                write_sample(writer, s)
            write_event(writer, 1.0, "mark")
            write_event(writer, 2.0, "battle_start")
        paths.append(path)

    samples, events = read_csvs(paths)
    trials, discarded = build_trials(events, reaction=0.0)

    assert len(samples) == 10
    assert [t.source for t in trials] == ["hotkey", "hotkey"]
    assert discarded == []
    assert [analyse_trial(samples, t).dist2 for t in trials] == [700.0, 700.0]
