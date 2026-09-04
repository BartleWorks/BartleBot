# Testing

What every change ships with, and the two ways this suite goes slow or flaky.

- Test doubles in `tests/conftest.py`: `FakeClient`, `FakeMachine`, `FakeEntity`
- No Wizard101 process needed — all node tests use `FakeClient`
- `FakeMachine.get_option()` returns 0 for unknown options to avoid real delays

## Keeping the suite fast

A node test that sleeps through a production cadence costs that time on every run, so
compress the constants the code under test reads — `monkeypatch.setattr` on the *module
that imported them*, not on `timing.py`. `_fast_settle` in `tests/test_deck_io.py` and
`fast` in `tests/test_auto_select.py` are the pattern.

Two things make this less obvious than it looks:

- **Windows schedules waits on a ~15.6 ms tick.** Every `asyncio.sleep` shorter than that
  costs a full tick anyway, so dropping an interval from 10 ms to 2 ms buys *nothing*. The
  session fixture `_fine_grained_timers` in `conftest.py` calls `timeBeginPeriod(1)` to
  drop the tick to ~1 ms, which is what makes short intervals worth compressing at all.
  Don't remove it: it was worth ~7 s across the suite on its own.
- **Compress cadences, never ceilings.** A poll interval can go as low as the tick allows,
  because the fakes it drives count iterations rather than seconds. A *ceiling* — a
  `timeout=`, a `wait_for`, a Safety Delay — is what the code races against, and cutting
  one does not make a test faster so much as change which branch it takes. Undershooting
  `AUTO_SELECT_TREE_TIMEOUT` sent the drive down its blind fallback path on CI, where the
  assertions about the sighted path no longer meant anything, and the test failed with no
  hint that it had stopped testing what it names.
- **A ceiling that something is read after is a race, too.** `timeout=0.05` on a call whose
  result the test then inspects passes alone and fails in parallel, when the cancel lands
  before the object exists — see `test_apply_deck_closes_the_builder_on_timeout`. Same for
  a window several polls have to fit inside: keep it an order of magnitude wider than the
  polls nominally cost, as `test_dungeon_warning_dialog_appears_late` now does.

These bite only under load, so they pass locally and fail on a 2-core CI runner. CI runs
`-n auto` rather than a fixed worker count for the same reason — a count picked on a
16-core machine oversubscribes a runner 4:1 and stretches every wall-clock test until it
breaks.

Tests whose *subject* is elapsed time are the exception and should stay: the `#114` guards
in `test_group_manager.py` assert a regroup pays one settle rather than three. Scale
`_SETTLE` and `_BUDGET` together if they get slow — only their ratio carries the meaning.

## One QApplication per worker

There is exactly one `QApplication` per xdist worker and it lives for the whole session,
so anything set *on it* — stylesheet, palette, style, application font — is process-wide
state shared with every test that lands in the same worker afterwards. `-n auto` with the
default `dist=load` hands out individual tests, so which tests share a process changes run
to run, and leaked global state surfaces as a flake rather than as a failure.

**A test that mutates process-global GUI state restores it.** `_restore_app_stylesheet`
in `conftest.py` does that for the stylesheet, because the theme tests legitimately need
`qapp.setStyleSheet(...)` — it is the only broadcast that reaches a themed child of a
plain parent — and four modules were leaving it set. It restores only when the value
actually changed: assigning one is a `StyleChange` delivered to every live widget, not
something to pay on 3,000 tests that never touch it.

**Do not assert finer than the mechanism can express.** An app stylesheet raises the
canvas viewport from 461px to 470px, which flips the parity of the centering indent
`QGraphicsView` applies when the scene fits, so the camera `exit_focus_mode` restores
lands one device pixel from where it started. `camera_state` is a transform plus two
*integer* scrollbars and that indent is integer too, so a pixel is the floor — but
`test_leaving_focus_mode_puts_the_camera_back` asserted `< 1.0` in *scene* units, and at
a zoomed-out fit one pixel is over nine of those. The assertion now converts to device
pixels, which also stops its strictness varying with the zoom it happens to measure at.

Neither of those was a race: with a stylesheet leaked the mismatch is 70/70 across
viewport heights, and without one it is 0/70. A GUI flake under `-n auto` is usually
order-dependent state, so look for what an earlier test left behind before reaching for
`processEvents` or a sleep.

`--dist loadfile` would hide this class of bug by making the accident stable. Order
independence is a property worth keeping, so CI stays on `load`.
