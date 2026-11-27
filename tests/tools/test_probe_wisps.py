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


def test_a_wisp_gone_beside_a_wizard_whose_ratio_rose_was_taken_with_the_distances_that_bracket_it():
    """The wizard stands still; the wisp comes at it, is last seen 40 off, and the health rises."""
    track = _line(1, (-200.0, 0.0), (100.0, 0.0), 1.6)  # last seen at x = -40
    wizard = _wizard(1.6, health=0.5) + _wizard(3.0, health=0.8, start=1.7)
    run = probe.Run(wizard=wizard, sightings=track)

    end = probe.ending(run, track)

    assert end.how == "taken"
    assert math.isclose(end.seen_at, 40.0, abs_tol=1e-6)
    assert math.isclose(end.gone_at, 40.0, abs_tol=1e-6)
    assert math.isclose(end.rose, 0.3, abs_tol=1e-6)
    assert (
        "taken: the wizard 40 off at its last sighting, 40 at the first without it; health rose 30%"
        in probe.summarize(run)
    )


def test_a_wisp_gone_far_from_the_wizard_is_gone_not_taken():
    track = _line(1, (2000.0, 0.0), (0.0, 0.0), 1.0)
    run = probe.Run(wizard=_wizard(3.0, health=0.5), sightings=track)

    assert probe.ending(run, track).how == "gone"


def test_a_wisp_gone_beside_a_wizard_whose_ratio_did_not_rise_is_gone():
    """A rise of the other kind's ratio is not this wisp's."""
    track = _line(1, (20.0, 0.0), (0.0, 0.0), 1.0, name="WispMana")
    wizard = _wizard(1.0, health=0.5, mana=0.5) + _wizard(3.0, health=0.9, mana=0.5, start=1.1)
    run = probe.Run(wizard=wizard, sightings=track)

    assert probe.ending(run, track).how == "gone"


def test_a_wisp_seen_to_the_end_is_still_there():
    track = _line(1, (0.0, 0.0), (0.0, 0.0), 3.0)
    run = probe.Run(wizard=_wizard(3.0), sightings=track)

    assert probe.ending(run, track).how == "still there"


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
