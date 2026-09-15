# Testing

What every change ships with, and the ways this suite goes slow or flaky.

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

## Not every CI-only failure is a timing one

The section above is about load, so it is the first thing reached for when CI fails and a
dev box does not — and it is the wrong answer for the other family. **A CI-only failure
that is deterministic, identical across parallel and serial runs, and off by the same
number of pixels every time is a style or font difference, not a race.** `-n auto` is
never implicated in one of these, and removing it changes nothing.

A dev box resolves the `windowsvista` style; a GitHub runner resolves `windows11`, whose
comboboxes, checkboxes and push buttons are wider at the same font metrics. Both sidebar
floors are measured from the live style — see `gui/screen_fit.left_sidebar_floor` — so on
a runner they come out 33px wider between them than `LEFT_SIDEBAR_MIN_WIDTH` and
`RIGHT_SIDEBAR_MIN_WIDTH`, the references the pure arithmetic returns when nothing is
measured. **A test comparing a measured widget floor against a literal, or against
`required_window_size` left at its defaults, is asserting which style the runner has.**
Give the arithmetic side the same measured floors the widget side got — `_declared_floor`
in `test_window_minimum.py` and `_sidebar_excess` in `test_screen_fit.py` are the pattern.

Ask the question locally instead of on the runner:

```bash
QT_STYLE_OVERRIDE=windows11 uv run pytest -n auto   # what CI resolves
```

That reproduces this whole class exactly, which is how the eleven failures across those
two modules were found without a push. Where a test can sweep the styles itself it should
— `test_the_player_card_fits_the_right_sidebar_under_every_available_style` is that, and
it exists because a 3px overflow reached CI the same way.

Tests whose *subject* is elapsed time are the exception and should stay: the regroup-settle
guards in `test_group_manager.py` assert a regroup pays one settle rather than three. Scale
`_SETTLE` and `_BUDGET` together if they get slow — only their ratio carries the meaning.

## Widget lifetime

**`deleteLater()` frees nothing in a test.** It posts a `DeferredDelete` event that only
arrives on an event-loop spin, and no Qt event loop runs during the suite. Left to itself
every widget every test builds stays alive for the whole session, and that is quadratic
rather than merely untidy: `QApplication.setStyleSheet` delivers a `StyleChange` to every
live widget, so one app-wide theme broadcast cost 26 s at 27,918 live widgets and 0.001 s
at zero. That single leak was most of an 8-minute `-n auto` run.

`_free_widgets_this_test_created` in `conftest.py` handles it: at teardown it closes and
destroys the *top-level* widgets that did not exist when it was set up, then flushes the
`DeferredDelete` queue by hand. A test does not need to call `deleteLater()` for its own
widgets — the ones already there are harmless, not load-bearing.

Three rules follow:

- **Popups are freed after their owners.** A `QCompleter`'s popup is a parentless window
  the completer holds by raw pointer and deletes itself, so freeing the popup first makes
  the owner free it twice: exit 127, no traceback, and only when `topLevelWidgets()`
  happens to list the popup first. The fixture frees everything else, flushes, then frees
  any popup still alive.
- **Reap per test, never globally.** A blanket sweep over `topLevelWidgets()` frees
  widgets a module- or session-scoped fixture is still holding, and the next
  `processEvents()` touches the corpse: exit 127, no traceback. Being autouse and
  function-scoped is what makes the fixture safe — anything a wider-scoped fixture built
  already existed when it was set up, so it survives.
- **A widget a test stashes somewhere longer-lived than the test will be freed under it.**
  If something genuinely has to outlive a test, it belongs to a module- or session-scoped
  fixture, which is exactly the boundary the reaping respects.

`pytest-qt` supplies the session-scoped `qapp` fixture the GUI tests request; the 47
hand-rolled copies of it are gone. Its `qtbot` is available for a test that wants signal
waiting or key/mouse simulation; `qtbot.addWidget` is redundant here, since the fixture
above already owns the lifetime of everything a test builds.

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

**Pumping can destroy the state you are pumping for.** Almost no test shows or activates
its windows, so a popup put up with `QMenu.popup()` has no active window behind it: spin the
loop under load and Windows deactivates it, the menu closes itself, and every teardown
hook — `aboutToHide`, and the parent hand-back `anchor_menu_to_window` connects to it —
has already fired by the time the assertion looks. The hand-back test in
`test_menu_anchoring.py` flaked exactly this way, and pumping *harder* — the
obvious fix, and the one the issue suggested — made it fail every time instead. Ask first whether the state arrives synchronously: `aboutToShow` is
emitted from inside `popup()`, so that half wanted no pump at all. `_pump_until` in
`test_menu_anchoring.py` is there for the tearing-down half, which genuinely does need one.

**`show()` does not give a widget focus; activation does, and it is the WM's answer.**
`QWidget.hasFocus()` is false unless the widget's window is *active*, and `show()` only
asks Windows to make it so — the answer arrives as a later event, on the WM's schedule.
On an idle dev box it lands inside the first `processEvents()`, which is why a test that
shows a window and calls `setFocus()` looks deterministic; under `-n auto` it can land
after the test is over, and every `hasFocus()` branch in the widget under test is skipped.
That is a silent pass-by-omission, not an error: `test_growing_scrolls_an_enclosing_page_to_the_caret`
failed `assert 0 > 0` because `GrowingTextEdit` correctly declined to scroll a field it
believed unfocused. `QApplication.setActiveWindow(window)` hands the activation over
synchronously and is the fix; pumping harder is not, since there is nothing pending to
pump. Then **assert the focus you arranged** — `_shown_page` in
`test_growing_text_edit.py` is the pattern, and it matters at both ends: a test asserting
that an *unfocused* widget stays put passes for free on a window that had no focus to give.

`--dist loadfile` would hide this class of bug by making the accident stable. Order
independence is a property worth keeping, so CI stays on `load`.
