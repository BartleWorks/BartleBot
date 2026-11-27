"""The analysis half of tools/probe_wisps.py: how wisps move, and what became of each, over made-up samples."""

import math
import sys

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tools import probe_wisps as probe  # noqa: E402

_STEP = 0.1


def _line(wisp: int, start: tuple[float, float], velocity: tuple[float, float], seconds: float, name="WC_WispHealth"):
    count = round(seconds / _STEP) + 1
    return [
        probe.Sighting(
            i * _STEP, wisp, name, (start[0] + velocity[0] * i * _STEP, start[1] + velocity[1] * i * _STEP, 0.0)
        )
        for i in range(count)
    ]


def _circle(wisp: int, radius: float, period: float, seconds: float, phase: float = 0.0, name="WC_WispHealth"):
    count = round(seconds / _STEP) + 1
    sightings = []
    for i in range(count):
        angle = phase + 2 * math.pi * i * _STEP / period
        sightings.append(
            probe.Sighting(i * _STEP, wisp, name, (radius * math.cos(angle), radius * math.sin(angle), 0.0))
        )
    return sightings


def _wizard(seconds: float, at=(0.0, 0.0, 0.0), health=1.0, mana=1.0, start=0.0):
    count = round((seconds - start) / _STEP) + 1
    return [probe.Wizard(start + i * _STEP, at, health, mana) for i in range(count)]


def test_a_wisp_that_barely_moves_is_still():
    track = [probe.Sighting(i * _STEP, 1, "WispMana", (i % 2 * 3.0, 0.0, i % 3 * 5.0)) for i in range(50)]

    m = probe.motion(track)

    assert m.shape == "still"
    assert m.rise == 10.0


def test_a_wisp_on_a_straight_line_heads_one_way_at_its_speed():
    m = probe.motion(_line(1, (0, 0), (60.0, 80.0), 10.0))

    assert m.shape == "heads one way"
    assert math.isclose(m.speed, 100.0, rel_tol=1e-6)
    assert math.isclose(m.stray, 1000.0, rel_tol=1e-6)
    assert m.lap is None


def test_a_wisp_round_a_circle_loops_and_its_lap_is_timed():
    m = probe.motion(_circle(1, radius=500.0, period=30.0, seconds=45.0))

    assert m.shape == "loops"
    assert math.isclose(m.lap, 30.0, abs_tol=0.2)
    assert math.isclose(m.speed, 2 * math.pi * 500 / 30, rel_tol=0.01)


def test_a_wisp_that_turns_back_and_forth_wanders():
    track = []
    t = 0.0
    x, y = 0.0, 0.0
    # A zigzag that never comes back to ground it held.
    for leg in range(6):
        dx, dy = (10.0, 40.0) if leg % 2 == 0 else (10.0, -40.0)
        for _ in range(10):
            track.append(probe.Sighting(t, 1, "WispHealth", (x, y, 0.0)))
            x, y, t = x + dx * 0.1, y + dy * 0.1, t + _STEP

    assert probe.motion(track).shape == "wanders"


def test_wisps_on_one_loop_share_its_path_and_one_elsewhere_does_not():
    tracks = {
        1: _circle(1, 500.0, 30.0, 40.0),
        2: _circle(2, 500.0, 30.0, 40.0, phase=2.0),
        3: _circle(3, 900.0, 30.0, 40.0),
    }

    assert probe.sharers(tracks, 1) == 1
    assert probe.sharers(tracks, 3) == 0


def _pickup_run(kind_seen="WC_WispHealth", rises="health"):
    """A still wizard at the origin; a wisp coming along x at 100/s from 800 off. At 400 off the
    wizard's *rises* ratio goes from 0.5 to 0.75 (410 off at the sample before); the wisp then flies
    in to rest 45 off, reached 0.8 s after the rise, and is seen last 1.4 s after it. The run goes on
    a second more."""
    wizard, sightings = [], []
    rise_at, gone_at, end_at = 40, 55, 65  # sample indexes, 0.1 s apart
    for i in range(end_at + 1):
        t = round(i * _STEP, 2)
        risen = i >= rise_at
        health = 0.75 if risen and rises == "health" else 0.5
        mana = 0.75 if risen and rises == "mana" else 0.5
        wizard.append(probe.Wizard(t, (0.0, 0.0, 0.0), health, mana))
        if i < gone_at:
            x = -800.0 + 100.0 * t if i < rise_at else min(-45.0, -400.0 + (355.0 / 9) * (i - rise_at + 1))
            sightings.append(probe.Sighting(t, 7, kind_seen, (x, 0.0, 20.0)))
    return probe.Run(wizard=wizard, sightings=sightings)


def test_a_rise_in_a_ratio_is_put_down_to_the_nearest_wisp_of_its_kind_with_how_far_off_it_was():
    run = _pickup_run()

    [pickup] = probe.pickups(run)

    assert pickup.wisp == 7
    assert pickup.kind == "health"
    assert math.isclose(pickup.rose, 0.25)
    assert math.isclose(pickup.off, 410.0, abs_tol=1e-6)  # at the sample before the rise
    assert math.isclose(pickup.rest, 45.0, abs_tol=1e-6)
    assert math.isclose(pickup.arrived, 0.8, abs_tol=1e-6)
    assert math.isclose(pickup.linger, 1.4, abs_tol=1e-6)


def test_the_summary_says_how_a_taken_wisp_came_in_and_lingered():
    summary = probe.summarize(_pickup_run())

    assert (
        "taken at 4.0 s: health rose 25% with it 410 off; it flew in, coming to rest 45 off 0.8 s after, "
        "and left the list 1.4 s after" in summary
    )
    assert "Pickups: 1, each wisp 410-410 off at the last sample before" in summary
    assert "taken wisps lingered in the list 1.4-1.4 s" in summary


def test_a_taken_wisp_moves_as_it_did_before_the_pickup_not_as_it_flew_in():
    summary = probe.summarize(_pickup_run())

    assert "heads one way, speed 100/s" in summary


def test_a_rise_with_no_wisp_of_its_kind_in_sight_is_put_down_to_none():
    """A potion, or a wisp unseen: a mana wisp in sight is not a health rise's."""
    run = _pickup_run(kind_seen="WC_WispMana", rises="health")

    [pickup] = probe.pickups(run)
    summary = probe.summarize(run)

    assert pickup.wisp is None
    assert "Rises with no wisp of the kind in sight (a potion?): health at 4.0 s" in summary
    assert "gone untaken at 5.4 s" in summary


def test_a_wisp_that_leaves_the_list_with_no_rise_is_gone_untaken():
    track = _line(1, (2000.0, 0.0), (0.0, 0.0), 1.0)
    run = probe.Run(wizard=_wizard(3.0, health=0.5), sightings=track)

    assert probe.pickups(run) == []
    assert "gone untaken at 1.0 s, 2000 off" in probe.summarize(run)


def test_a_wisp_seen_to_the_end_is_still_there():
    track = _line(1, (0.0, 0.0), (0.0, 0.0), 3.0)
    run = probe.Run(wizard=_wizard(3.0), sightings=track)

    assert "still there at the end" in probe.summarize(run)


def test_a_full_wizard_a_wisp_passes_through_is_reported_with_the_nearest_it_came():
    track = _line(1, (-500.0, 5.0), (100.0, 0.0), 10.0)
    run = probe.Run(wizard=_wizard(10.0, health=1.0), sightings=track)

    assert math.isclose(probe.nearest_while_full(run, track), 5.0, abs_tol=1e-6)
    assert "a full wizard came within 5 and it stayed" in probe.summarize(run)


def test_a_wizard_below_full_is_not_counted_as_full():
    track = _line(1, (-500.0, 5.0), (100.0, 0.0), 10.0)
    run = probe.Run(wizard=_wizard(10.0, health=0.9), sightings=track)

    assert probe.nearest_while_full(run, track) is None


def test_a_full_wizard_far_from_every_wisp_gets_no_line():
    track = _line(1, (5000.0, 0.0), (0.0, 0.0), 3.0)
    run = probe.Run(wizard=_wizard(3.0), sightings=track)

    assert "full wizard" not in probe.summarize(run)


def test_the_kind_comes_from_the_template_name():
    assert probe.kind_of("WC_WispHealth") == "health"
    assert probe.kind_of("KT_WispMana_02") == "mana"
    assert probe.kind_of("WispGold") == "other"


def test_a_run_round_trips_through_its_csv(tmp_path):
    path = tmp_path / "run.csv"
    path.write_text(
        "t,what,id,name,x,y,z,health,mana\n"
        "0,zone,,WizardCity/WC_Hub,0,0,0,,\n"
        "0.00,wizard,,,1.0,2.0,3.0,0.5000,1.0000\n"
        "0.00,wisp,42,WC_WispHealth,10.0,20.0,30.0,,\n",
        encoding="utf-8",
    )

    run = probe.read_csv(path)

    assert run.zone == "WizardCity/WC_Hub"
    assert run.wizard == [probe.Wizard(0.0, (1.0, 2.0, 3.0), 0.5, 1.0)]
    assert run.sightings == [probe.Sighting(0.0, 42, "WC_WispHealth", (10.0, 20.0, 30.0))]
    assert "1 wisps in WizardCity/WC_Hub" in probe.summarize(run)


def test_no_wisp_seen_says_so():
    assert probe.summarize(probe.Run(wizard=_wizard(2.0))).startswith("No wisp seen")


def test_wisps_seen_for_less_than_a_lap_of_a_shared_bending_path_follow_a_path():
    """Half a big circle: not straight enough to head one way, and not a whole lap."""
    run = probe.Run(
        wizard=_wizard(20.0),
        sightings=_circle(1, 1000.0, 40.0, 20.0) + _circle(2, 1000.0, 40.0, 20.0, phase=0.5),
    )

    summary = probe.summarize(run)

    assert summary.count("follows a path") == 2
    assert "wanders" not in summary
