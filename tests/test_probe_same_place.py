"""The analysis half of tools/probe_same_place.py: reading radius, realm, battle, arrival and cost."""

import csv

from tools.probe_same_place import (
    CSV_FIELDS,
    Point,
    Reading,
    arrival_views,
    asymmetries,
    battle_split,
    find_arrivals,
    find_crossings,
    gid_changes,
    group_ticks,
    near_misses,
    never_seen_close,
    pair_observations,
    read_csv,
    read_csvs,
    summarise,
    write_reading,
)

ZONE = "WizardCity/WC_Streets/WC_Unicorn"


def _r(t, slot, x=0.0, *, seen=(), zone=ZONE, loading=False, battle=False, gid_seq=0, check_s=0.01, entities=50):
    return Reading(
        t=t,
        slot=slot,
        zone=zone,
        pos=Point(x, 0.0, 0.0),
        loading=loading,
        in_battle=battle,
        gid_seq=gid_seq,
        self_seen=True if seen is not None else None,
        seen=None if seen is None else frozenset(seen),
        check_s=check_s if seen is not None else None,
        entities=entities if seen is not None else None,
    )


def _walk_away(distances, seen_until, dt=0.5):
    """P1 still at the origin, P2 at each distance in turn; they see each other up to seen_until."""
    out = []
    for i, d in enumerate(distances):
        visible = d <= seen_until
        out += [_r(i * dt, 1, 0.0, seen={2} if visible else ()), _r(i * dt, 2, d, seen={1} if visible else ())]
    return out


def test_a_walk_out_and_back_gives_one_vanish_and_one_appear_per_direction():
    readings = _walk_away([1000, 3000, 4000, 4300, 5000, 4300, 4000, 3000], seen_until=4142)
    crossings = find_crossings(pair_observations(group_ticks(readings)))

    assert [(c.a, c.b, c.kind) for c in crossings] == [
        (1, 2, "vanish"),
        (2, 1, "vanish"),
        (1, 2, "appear"),
        (2, 1, "appear"),
    ]
    vanish = crossings[0]
    assert (vanish.dist_before, vanish.dist_after) == (4000, 4300)


def test_a_gap_longer_than_max_gap_is_not_a_crossing():
    readings = [_r(0, 1, seen={2}), _r(0, 2, 100, seen={1}), _r(10, 1, seen=()), _r(10, 2, 100, seen=())]
    assert find_crossings(pair_observations(group_ticks(readings))) == []


def test_loading_and_failed_checks_are_not_observations():
    readings = [
        _r(0, 1, seen=None),  # check failed: P1 observes nothing
        _r(0, 2, 100, seen={1}, loading=True),  # loading: neither side is observed
        _r(1, 1, seen={2}),
        _r(1, 2, 100, seen={1}, zone="Elsewhere"),  # different zones are not a pair
    ]
    assert pair_observations(group_ticks(readings)) == []


def test_a_close_pair_never_seen_is_flagged_as_a_realm_suspect():
    readings = [_r(t, 1, 0.0, seen=()) for t in range(5)] + [_r(t, 2, 50.0, seen=()) for t in range(5)]
    flagged = never_seen_close(pair_observations(group_ticks(readings)), near=2000)

    assert flagged == [(1, 2, ZONE, 50.0, 5), (2, 1, ZONE, 50.0, 5)]


def test_a_pair_seen_once_is_not_a_realm_suspect():
    readings = [_r(0, 1, seen={2}), _r(0, 2, 50, seen={1}), _r(1, 1, seen=()), _r(1, 2, 50, seen=())]
    assert never_seen_close(pair_observations(group_ticks(readings)), near=2000) == []


def test_a_one_sided_reading_is_reported_as_an_asymmetry():
    readings = [_r(0, 1, seen={2}), _r(0, 2, 4100, seen=()), _r(1, 1, seen={2}), _r(1, 2, 100, seen={1})]
    both, disagree = asymmetries(pair_observations(group_ticks(readings)))

    assert both == 2
    assert disagree == [(0, 1, 2, 4100)]


def test_battle_split_counts_near_targets_by_whether_they_are_fighting():
    readings = [
        _r(0, 1, seen={2}),
        _r(0, 2, 100, seen={1}, battle=True),
        _r(1, 1, seen=()),
        _r(1, 2, 100, seen={1}, battle=True),
        _r(2, 1, seen={2}),
        _r(2, 2, 100, seen={1}),
        _r(3, 1, seen=()),
        _r(3, 2, 9000, seen=()),  # too far to count
    ]
    split = battle_split(pair_observations(group_ticks(readings)), near=2000)

    # Only P1 observing counts for the in-battle half: P2 observing P1 is ruled out while P2 fights.
    assert split[True] == (1, 2)
    assert split[False] == (2, 2)


def test_arrival_after_a_load_times_when_the_others_appear():
    readings = [
        _r(0, 1, seen=()),
        _r(0, 2, 5000, seen=(), zone="Old"),
        _r(1, 1, seen=()),
        _r(1, 2, seen=(), loading=True, zone="Old"),
        _r(2, 1, seen={2}),  # P1 sees P2 while P2's screen is still up
        _r(2, 2, seen=(), loading=True, zone="Old"),
        _r(3, 1, seen={2}),
        _r(3, 2, 10, seen=()),
        _r(4, 1, seen={2}),
        _r(4, 2, 10, seen={1}),
    ]
    ticks = group_ticks(readings)
    arrivals = find_arrivals(ticks)

    assert [(a.slot, a.kind, a.start, a.t) for a in arrivals] == [(2, "load", 1, 3)]
    views = {v.direction: v for v in arrival_views(ticks, arrivals)}
    assert views["others see arriver"].lag == -1
    assert views["arriver sees others"].lag == 1


def test_arrival_with_nobody_appearing_has_no_lag():
    readings = [
        _r(0, 1, seen=()),
        _r(0, 2, seen=(), loading=True),
        _r(1, 1, seen=()),
        _r(1, 2, 10, seen=()),
    ]
    ticks = group_ticks(readings)
    (view, _back) = arrival_views(ticks, find_arrivals(ticks))

    assert view.lag is None and not view.never_lost


def test_a_jump_seen_on_landing_has_zero_lag_even_if_the_pair_later_parts():
    readings = [
        _r(0, 1, seen={2}),
        _r(0, 2, 3000, seen={1}),
        _r(1, 1, seen={2}),
        _r(1, 2, 10, seen={1}),  # the teleport
        _r(2, 1, seen=()),
        _r(2, 2, 10, seen=(), zone="Elsewhere"),
    ]
    ticks = group_ticks(readings)
    arrivals = find_arrivals(ticks)

    assert [(a.slot, a.kind) for a in arrivals] == [(2, "jump")]
    views = [v for v in arrival_views(ticks, arrivals) if v.arrival.kind == "jump"]
    assert [(v.lag, v.dist) for v in views] == [(0, 10), (0, 10)]


def test_sustained_fast_movement_is_a_mount_not_a_run_of_jumps():
    xs = [0, 0, 600, 1200, 1800, 2400, 2400]
    readings = [_r(i, 1, x, seen=()) for i, x in enumerate(xs)]
    assert find_arrivals(group_ticks(readings)) == []


def test_the_window_ends_when_the_arriver_moves_on():
    readings = [
        _r(0, 1, seen=()),
        _r(0, 2, seen=(), loading=True),
        _r(1, 1, seen=()),
        _r(1, 2, 100, seen=()),  # arrives near P1, which it never sees
        _r(2, 1, seen=()),
        _r(2, 2, 100, seen=(), loading=True),  # leaves
        _r(3, 1, seen={2}),
        _r(3, 2, 5, seen={1}),  # back again: this appearance belongs to the second visit
    ]
    ticks = group_ticks(readings)
    first = [v for v in arrival_views(ticks, find_arrivals(ticks)) if v.arrival.t == 1]

    assert first and all(v.lag is None and not v.never_lost for v in first)


def test_near_misses_skip_realm_suspects_and_say_how_long_since_an_arrival():
    readings = [
        _r(0, 1, seen=()),
        _r(0, 2, seen=(), loading=True),
        _r(0, 3, 50, seen=()),
        _r(1, 1, seen=()),
        _r(1, 2, 100, seen={3}),
        _r(1, 3, 50, seen={2}),
        _r(2, 1, seen=()),
        _r(2, 2, 100, seen={3}),
        _r(2, 3, 50, seen={2}),
    ]
    ticks = group_ticks(readings)
    obs = pair_observations(ticks)
    misses = near_misses(obs, find_arrivals(ticks), near=2000, excluded={(1, 3), (3, 1)})

    assert misses == [(1, 1, 2, 100, 1), (1, 2, 1, 100, 1), (2, 1, 2, 100, 2), (2, 2, 1, 100, 2)]


def test_gid_changes_say_whether_the_client_moved_zone():
    readings = [
        _r(0, 1, seen=()),
        _r(1, 1, seen=(), gid_seq=1, zone="Other"),
        _r(2, 1, seen=(), gid_seq=2, zone="Other"),
    ]
    assert gid_changes(group_ticks(readings)) == [(1, 1, True), (1, 2, False)]


def test_csv_round_trip_keeps_every_field(tmp_path):
    readings = [
        _r(0.5, 1, 12.5, seen={2, 3}),
        _r(0.5, 2, seen=None, loading=True),
        _r(0.5, 3, seen=(), battle=True, gid_seq=4),
    ]
    path = tmp_path / "run.local.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_FIELDS)
        for r in readings:
            write_reading(writer, r)

    assert read_csv(path) == readings


def test_pooled_runs_do_not_overlap_in_time(tmp_path):
    paths = []
    for name in ("a", "b"):
        path = tmp_path / f"{name}.local.csv"
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_FIELDS)
            write_reading(writer, _r(0, 1, seen=()))
            write_reading(writer, _r(5, 1, seen=()))
        paths.append(path)

    times = [r.t for r in read_csvs(paths)]
    assert times == sorted(times) and len(set(times)) == 4


def test_summary_covers_every_question_and_names_no_one():
    text = summarise(_walk_away([1000, 3000, 4000, 4300, 5000, 4300, 4000, 3000], seen_until=4142))

    for heading in ("1. RADIUS", "REALM", "SYMMETRY", "2. BATTLE", "3. ARRIVAL", "4. COST", "GID STABILITY"):
        assert heading in text
    assert "vanish P1->P2 4000..4300" in text
