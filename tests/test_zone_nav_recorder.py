"""Unit tests for the zone navigation tester's transition recorder."""

from tools.zone_nav_tester.recorder import HUB, LIST, OBJECT, RIDE, WALK, Recorder, Sample

_A, _B, _BOAT = "W/A", "W/B", "W/Boat"


def _feed(rec: Recorder, samples: list[Sample]) -> list:
    out = []
    for sample in samples:
        out.extend(rec.feed(sample))
    return out


def _at(t: float, zone: str | None, x: float, **flags) -> Sample:
    return Sample(t, zone, (x, 0.0, 0.0), **flags)


def _load(t: float, x: float) -> Sample:
    return _at(t, None, x, loading=True)


def test_a_walk_records_its_trigger_a_reset_well_back_and_where_it_landed():
    [t] = _feed(
        Recorder(),
        [_at(0.0, _A, 0), _at(0.2, _A, 200), _at(0.4, _A, 400), _at(0.6, _A, 500), _load(0.8, 500), _at(3.0, _B, 9)],
    )

    assert (t.kind, t.from_zone, t.to_zone) == (WALK, _A, _B)
    assert t.trigger_xyz == (500, 0.0, 0.0)
    assert t.far_xyz == (200, 0.0, 0.0)
    assert t.arrival_xyz == (9, 0.0, 0.0)


def test_the_interact_prompt_makes_it_an_object():
    [t] = _feed(
        Recorder(),
        [
            _at(0.0, _A, -400),
            _at(0.2, _A, -200),
            _at(0.4, _A, 30, prompt=True),
            _at(0.6, _A, 31, prompt=True),
            _load(0.8, 31),
            _at(3.0, _B, 0),
        ],
    )

    assert t.kind == OBJECT
    assert t.prompt_xyz == (31, 0.0, 0.0)
    # The last spot before the prompt came up: somewhere it does not show.
    assert t.pre_prompt_xyz == (-200, 0.0, 0.0)


def test_standing_still_while_the_prompt_comes_up_is_not_a_reset():
    """The spot you were standing on when the prompt appeared is where it shows."""
    [t] = _feed(
        Recorder(),
        [
            _at(0.0, _A, -500),
            _at(0.2, _A, 30),
            _at(0.4, _A, 30),
            _at(0.6, _A, 30, prompt=True),
            _load(0.8, 30),
            _at(3.0, _B, 0),
        ],
    )

    assert t.pre_prompt_xyz == (-500, 0.0, 0.0)


def test_no_spot_clear_of_the_object_means_no_reset():
    [t] = _feed(Recorder(), [_at(0.0, _A, 30), _at(0.2, _A, 30, prompt=True), _load(0.4, 30), _at(3.0, _B, 0)])

    assert t.prompt_xyz == (30, 0.0, 0.0)
    assert t.pre_prompt_xyz is None


def test_an_open_zone_list_makes_it_a_list():
    [t] = _feed(
        Recorder(),
        [_at(0.0, _A, 0), _at(0.2, _A, 5, prompt=True), _at(0.4, _A, 5, list_open=True), _load(0.6, 5), _at(3, _B, 0)],
    )

    assert t.kind == LIST


def test_the_hub_button_greying_out_makes_it_the_hub_button():
    [t] = _feed(
        Recorder(),
        [_at(0.0, _A, 0, hub_enabled=True), _at(0.2, _A, 0, hub_enabled=False), _load(0.4, 0), _at(3, _B, 0)],
    )

    assert t.kind == HUB


def test_a_button_already_cooling_down_does_not_make_a_walk_a_hub_use():
    [t] = _feed(Recorder(), [_at(0.0, _A, 0), _at(0.2, _A, 50), _load(0.4, 50), _at(3, _B, 0)])

    assert t.kind == WALK


def test_the_speedhack_greying_the_hub_button_for_one_tick_does_not_make_a_walk_a_hub_use():
    """Confirmed live: under the speedhack, a plain walk reads hub_enabled True for the whole
    signal window and then False on the single sample just before loading, every time -- the
    same shape a real click leaves. speed_boosted on that last sample is what tells them apart.
    """
    [t] = _feed(
        Recorder(),
        [
            _at(0.0, _A, 0, hub_enabled=True, speed_boosted=True),
            _at(0.2, _A, 50, hub_enabled=False, speed_boosted=True),
            _load(0.4, 50),
            _at(3, _B, 0),
        ],
    )

    assert t.kind == WALK


def test_a_prompt_long_before_the_load_does_not_count():
    """Walking past an NPC on the way to a gate is still a walk."""
    [t] = _feed(Recorder(), [_at(0.0, _A, 0, prompt=True), _at(5.0, _A, 900), _load(5.2, 900), _at(8, _B, 0)])

    assert t.kind == WALK


def test_a_ride_is_one_transition_through_its_cutscene_zone():
    rec = Recorder(may_be_ride_zone=lambda zone: zone == _BOAT)
    out = _feed(
        rec,
        [
            _at(0.0, _A, 0),
            _at(0.2, _A, 10, prompt=True),
            _load(0.4, 10),
            _at(3.0, _BOAT, 5000),
            _at(4.0, _BOAT, 5010),
            _load(5.0, 5010),
            _at(8.0, _B, 77),
        ],
    )

    [t] = out
    assert (t.kind, t.from_zone, t.to_zone, t.ride_zone) == (RIDE, _A, _B, _BOAT)
    assert t.prompt_xyz == (10, 0.0, 0.0)
    assert t.arrival_xyz == (77, 0.0, 0.0)


def test_walking_around_a_possible_cutscene_zone_releases_the_transition_into_it():
    rec = Recorder(may_be_ride_zone=lambda zone: zone == _BOAT)
    held = _feed(rec, [_at(0.0, _A, 0), _at(0.2, _A, 10, prompt=True), _load(0.4, 10), _at(3.0, _BOAT, 5000)])
    assert held == []

    [t] = _feed(rec, [_at(4.0, _BOAT, 5400)])

    assert (t.kind, t.to_zone) == (OBJECT, _BOAT)


def test_a_walk_into_an_unmapped_zone_is_reported_at_once():
    """Rides start at an interaction, so only those are held back."""
    rec = Recorder(may_be_ride_zone=lambda zone: True)

    [t] = _feed(rec, [_at(0.0, _A, 0), _load(0.2, 0), _at(3.0, _BOAT, 0)])

    assert t.kind == WALK


def test_a_load_that_ends_where_it_began_is_not_a_transition():
    assert _feed(Recorder(), [_at(0.0, _A, 0), _load(0.2, 0), _at(3.0, _A, 0), _at(3.2, _A, 1)]) == []


def test_the_first_zone_seen_is_not_an_arrival():
    assert _feed(Recorder(), [_at(0.0, _A, 0), _at(0.2, _A, 1)]) == []


def test_reset_forgets_a_half_seen_transition():
    rec = Recorder()
    _feed(rec, [_at(0.0, _A, 0), _load(0.2, 0)])

    rec.reset()

    assert _feed(rec, [_at(3.0, _B, 0)]) == []
