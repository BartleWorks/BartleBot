@../../bartleby-core/tests/CLAUDE.md

# Testing

What every change ships with here. The ways this suite goes slow or flaky are core's
`tests/CLAUDE.md`, imported above.

- **Where a test goes:** the test for `bartlebot/a/b/c.py` is `tests/a/b/test_c.py`. A
  test named `test_<module>_<aspect>.py`, or one that drives a module through another
  name, sits beside that module's test. Tests of a top-level module (`app_controller`,
  `settings_manager`, `paths`), of `bartlebot.py` (`test_startup.py`), and of the whole
  repo (shipped bots and templates, install-dir writes, integration) stay in `tests/`; a `tools/`
  script's test goes in `tests/tools/`, and our wizsprinter fork's in `tests/wizsprinter/`.
  A new folder needs an empty `__init__.py`, or pytest quietly stops collecting it.
  Shared helpers (`conftest.py`, `picker_helpers.py`, `port_sim.py`) stay at `tests/`.
  A drag on a card list is core's `bartleby.qt.testing.drag_helpers`.

- Test doubles in `tests/conftest.py`: `FakeClient`, `FakeMachine`, `FakeEntity`
- No Wizard101 process needed — all node tests use `FakeClient`
- `FakeMachine.get_option()` returns 0 for unknown options to avoid real delays
- **What a strategy casts** is a `tests/strategies/fight.py` test: describe a `Fight` (the
  team in slot order and which one is me, the enemies, the round, the hand, the pips, what
  hangs on whom) and `await cast(dsl, fight)` runs wizsprinter's real `handle_round` over
  it and returns the `Turn`. `shipped_dsl(id)` reads a shipped player strategy. A round
  costs about 1 ms, so cover each situation with its own fight rather than one long test.
  The fakes beneath it, at the memory reads, are `tests/wizsprinter/fakes.py`, which the
  wizsprinter tests share.
- A new dialog, card or row builder ships with a test that asks for the `parentless_shows`
  fixture, builds the surface, and asserts the list is empty. It catches a widget shown
  before it has a parent, which flashes a window on screen and leaves nothing in the
  finished tree to inspect. Core's `bartleby/qt/CLAUDE.md` has the rule;
  `test_building_the_dialog_never_shows_a_parentless_widget` is the pattern.
