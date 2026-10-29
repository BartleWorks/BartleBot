"""Checking a DSL away from the GUI: the checker, a surface's deferred checks, and the surfaces using them.

What matters is *where the parse ran*, so the tests here record every parse in this
process: ``combat_runner._checked`` is the one place a parse happens, and it is wrapped to
note the thread it ran on. A check run in the child process never reaches it, which is
the point -- nothing parses here at all.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import pytest
from bartleby.shared.strategy_models import PlayerCombatStrategy

from bartlebot.combat.strategy_store import StrategyKind, save_player_strategy
from bartlebot.combat.strategy_workset import StrategyWorkingSet
from bartlebot.content import store_roots
from bartlebot.gui.combat_strategies import dsl_checks
from bartlebot.gui.combat_strategies.dialog import CombatStrategyDialog
from bartlebot.gui.combat_strategies.dsl_checks import DeferredDslChecks, DslChecker, checker
from bartlebot.gui.combat_strategies.player_editor import PlayerStrategyEditor
from bartlebot.nodes import combat_runner
from bartlebot.nodes.combat_runner import dsl_check, known_dsl_check

_BROKEN = "not even close to valid (("
_VALID = "any<damage> @ enemy"
_WAIT_MS = 15_000


@pytest.fixture
def parses(monkeypatch):
    """Every parse in this process, with the thread it ran on; the in-memory cache starts empty."""
    seen: list[tuple[str, threading.Thread]] = []
    real = combat_runner._checked

    def recording(dsl):
        seen.append((dsl, threading.current_thread()))
        return real(dsl)

    monkeypatch.setattr(combat_runner, "_checked", recording)
    dsl_check.cache_clear()
    yield seen
    dsl_check.cache_clear()


def _on_gui_thread(seen) -> list[str]:
    return [dsl for dsl, thread in seen if thread is threading.main_thread()]


class _CountingExecutor(ThreadPoolExecutor):
    def __init__(self) -> None:
        super().__init__(max_workers=1)
        self.submitted: list[str] = []

    def submit(self, fn, /, *args, **kwargs):
        self.submitted.append(args[0])
        return super().submit(fn, *args, **kwargs)


# ---------------------------------------------------------------------------
# The checker
# ---------------------------------------------------------------------------


def test_a_known_dsl_is_answered_at_once(qapp, parses):
    dsl_check(_VALID)
    parses.clear()

    assert checker().request(_VALID) == dsl_check(_VALID)
    assert parses == []


def test_an_unknown_dsl_is_checked_in_another_process_and_announced_on_the_gui_thread(qtbot, parses):
    announced: list[tuple[str, threading.Thread]] = []
    checker().checked.connect(lambda dsl: announced.append((dsl, threading.current_thread())))

    assert checker().request(_BROKEN) is None
    qtbot.waitUntil(lambda: any(dsl == _BROKEN for dsl, _t in announced), timeout=_WAIT_MS)

    assert parses == [], "parsed in the child, not in this process"
    assert all(thread is threading.main_thread() for _dsl, thread in announced)
    error = known_dsl_check(_BROKEN).error
    assert error is not None and error == combat_runner.check_dsl_uncached(_BROKEN).error
    assert not checker().is_pending(_BROKEN)


def test_one_dsl_asked_for_twice_is_checked_once(qtbot, parses):
    pool = _CountingExecutor()
    own = DslChecker(pool)
    dsl = "any<blade> @ self | pass"

    own.request(dsl)
    own.request(dsl)
    qtbot.waitUntil(lambda: not own.is_pending(dsl), timeout=_WAIT_MS)

    assert pool.submitted == [dsl]
    assert known_dsl_check(dsl) is not None


def test_a_pool_that_will_not_take_work_falls_back_to_a_thread(qtbot, parses):
    pool = _CountingExecutor()
    pool.shutdown()
    own = DslChecker(pool)

    own.request(_BROKEN)
    qtbot.waitUntil(lambda: not own.is_pending(_BROKEN), timeout=_WAIT_MS)

    assert known_dsl_check(_BROKEN).error is not None
    assert _on_gui_thread(parses) == []


def test_a_child_that_dies_is_replaced_by_a_thread_and_the_check_runs_again(qtbot, parses):
    class _DeadPool(_CountingExecutor):
        def submit(self, fn, /, *args, **kwargs):
            self.submitted.append(args[0])
            future: Future = Future()
            future.set_exception(BrokenProcessPool("the child died"))
            return future

    own = DslChecker(_DeadPool())

    own.request(_BROKEN)
    qtbot.waitUntil(lambda: not own.is_pending(_BROKEN), timeout=_WAIT_MS)

    assert known_dsl_check(_BROKEN).error is not None
    assert [thread.name.startswith("dsl-check") for _dsl, thread in parses] == [True]


def test_every_check_waiting_on_a_child_that_dies_moves_to_one_thread(qtbot, parses):
    class _DyingPool(_CountingExecutor):
        def __init__(self) -> None:
            super().__init__()
            self.waiting: list[Future] = []

        def submit(self, fn, /, *args, **kwargs):
            self.submitted.append(args[0])
            future: Future = Future()
            self.waiting.append(future)
            return future

    pool = _DyingPool()
    own = DslChecker(pool)
    other = "any<blade> @ self"
    own.request(_BROKEN)
    own.request(other)

    for future in pool.waiting:
        future.set_exception(BrokenProcessPool("the child died"))
    qtbot.waitUntil(lambda: not own.is_pending(_BROKEN) and not own.is_pending(other), timeout=_WAIT_MS)

    assert known_dsl_check(_BROKEN) is not None and known_dsl_check(other) is not None
    assert len({thread for _dsl, thread in parses}) == 1, "one fallback thread, not one per check"


def test_the_checker_is_made_once(qapp):
    assert dsl_checks.checker() is dsl_checks.checker()


# ---------------------------------------------------------------------------
# Deferred checks
# ---------------------------------------------------------------------------


def test_an_unknown_dsl_reads_as_valid_and_its_card_is_redrawn_once_it_turns_out_broken(qtbot, parses):
    owner = PlayerStrategyEditor()
    redrawn: list[set] = []
    checks = DeferredDslChecks(owner, redrawn.append)

    assert checks.validator("card-a")(_BROKEN) is None, "not known yet, so drawn as fine"
    assert checks.validator("card-b")(_VALID) is None
    checks.request_unknown()

    qtbot.waitUntil(lambda: bool(redrawn), timeout=_WAIT_MS)
    assert redrawn == [{"card-a"}], "the card drawn from the valid DSL is left alone"
    assert _on_gui_thread(parses) == []
    assert checks.validator("card-a")(_BROKEN) is not None, "and redrawn, it is known to be broken"


def test_a_dsl_that_parses_costs_no_redraw(qtbot, parses):
    owner = PlayerStrategyEditor()
    redrawn: list[set] = []
    checks = DeferredDslChecks(owner, redrawn.append)
    checks.validator("card")(_VALID)
    checks.request_unknown()

    qtbot.waitUntil(lambda: not checks.unknown, timeout=_WAIT_MS)
    qtbot.wait(20)
    assert redrawn == []


def test_reset_forgets_what_was_drawn(qapp, parses):
    checks = DeferredDslChecks(PlayerStrategyEditor(), lambda _keys: None)
    checks.validator("card")(_BROKEN)

    checks.reset()

    assert checks.unknown == frozenset()


# ---------------------------------------------------------------------------
# The surfaces
# ---------------------------------------------------------------------------


@pytest.fixture
def roots(tmp_path, monkeypatch):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    for root in (shipped, user):
        for kind in ("player", "duel", "decks"):
            (root / "strategies" / kind).mkdir(parents=True)
        (root / "bot_strategies").mkdir(parents=True)
    monkeypatch.setattr(store_roots, "project_root", lambda: shipped)
    monkeypatch.setattr(store_roots, "product_dir", lambda _product: user)
    return shipped, user


def test_the_strategy_dialog_opens_without_parsing_and_marks_a_broken_card_after(qtbot, roots, parses):
    shipped, user = roots
    broken = PlayerCombatStrategy(name="Broken", dsl=_BROKEN)
    fine = PlayerCombatStrategy(name="Fine", dsl=_VALID)
    save_player_strategy(broken, shipped)
    save_player_strategy(fine, shipped)

    dlg = CombatStrategyDialog(StrategyWorkingSet.from_store(root=shipped, user_root=user), kind=StrategyKind.PLAYER)
    page = dlg.page(StrategyKind.PLAYER)

    assert _on_gui_thread(parses) == [], "drawing the list parsed nothing on the GUI thread"
    qtbot.waitUntil(lambda: page.card_for(broken.id)._invalid, timeout=_WAIT_MS)
    assert not page.card_for(fine.id)._invalid
    assert _on_gui_thread(parses) == []


def test_typing_in_the_dsl_box_never_parses_on_the_gui_thread(qtbot, roots, parses):
    shipped, user = roots
    pcs = PlayerCombatStrategy(name="Blade", dsl=_VALID)
    save_player_strategy(pcs, shipped)
    dlg = CombatStrategyDialog(
        StrategyWorkingSet.from_store(root=shipped, user_root=user), kind=StrategyKind.PLAYER, current_id=pcs.id
    )
    page = dlg.page(StrategyKind.PLAYER)
    editor = page.editor
    parses.clear()

    half_typed = ("any<d", "any<damage> @")
    for typed in (*half_typed, _BROKEN):
        editor.dsl_edit.setPlainText(typed)  # every keystroke redraws the card
    assert _on_gui_thread(parses) == []

    editor._validate_timer.stop()
    editor._check_in_background()  # the debounce firing
    qtbot.waitUntil(lambda: not editor._dsl_error_lbl.isHidden(), timeout=_WAIT_MS)
    qtbot.waitUntil(lambda: page.card_for(pcs.id)._invalid, timeout=_WAIT_MS)
    assert _on_gui_thread(parses) == []
    assert [known_dsl_check(text) for text in half_typed] == [None, None], "only the text the debounce saw"


def test_a_check_for_text_since_typed_over_is_not_shown(qtbot, parses):
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl=_VALID))
    editor.dsl_edit.setPlainText(_BROKEN)
    editor._validate_timer.stop()
    editor._check_in_background()
    editor.dsl_edit.setPlainText(_VALID)  # typed over before the check came back

    qtbot.waitUntil(lambda: known_dsl_check(_BROKEN) is not None, timeout=_WAIT_MS)
    qtbot.wait(20)
    assert editor._dsl_error_lbl.isHidden()
