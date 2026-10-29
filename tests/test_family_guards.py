"""The family's guards, run over BartleBot (bartleby-core's ``testing/guards.py``).

Each convention that can be read from the source is a function in core, and every product
runs it over its own package; core's ``docs/ARCHITECTURE.md`` has why. They were BartleBot's
own tests until Stage 3 of #167 moved them into core, and the allowlists below are the ones
they had, keyed as they were, against ``bartlebot/gui/`` for the button and theme guards.

An allowlist entry names one widget and why it is exempt, so the next person meets a
decision rather than a judgment call; an entry that matches nothing fails too.
"""

from __future__ import annotations

from bartleby.qt.testing import guards
from bartleby.runtime.testing.guards import core_copies, fixed_key_presses

from bartlebot.paths import project_root

PACKAGE = project_root() / "bartlebot"
GUI_ROOT = PACKAGE / "gui"

#: Bare ``QPushButton``\ s, keyed ``"<module>:<name>"`` under ``bartlebot/gui/``, and why each
#: is not an ``ActionButton``.
ALLOWED_PUSH_BUTTONS: dict[str, str] = {
    "settings/theme_tab.py:_reset_btn": (
        "Reset to System Default, styled with hardcoded colors on purpose — it is the "
        "way out of a custom theme that has broken its own tokens, so it has to stay "
        "legible exactly when THEME is what cannot be trusted. An ActionButton derives "
        "every color it draws from THEME and so cannot promise that."
    ),
    "settings/theme_tab.py:btn": (
        "A color swatch in the token editor: the background *is* the data — it shows "
        "the value of the token the label names, and its text is that hex value."
    ),
    "canvas/param_panel.py:btn": (
        "A region color swatch in the inspector — the background is the data. 22px "
        "square, no label, one of six in a palette row."
    ),
    "canvas/edit_scene.py:btn": (
        "The same region color swatch, in the region-properties dialog — the background is the data."
    ),
    "canvas/bot_canvas.py:_warning_pill": (
        "A status pill in the toolbar that happens to be clickable, not a verb. It "
        "reports orphaned nodes and reads as a badge beside the error pill; button "
        "chrome would make a passive count look like an action."
    ),
}

#: Modules allowed more than one ``ActionStyle.PRIMARY``, and why. Module is a proxy for
#: surface, and where it is not, the fix is an entry naming both surfaces, not a weaker
#: check. Empty is the goal, and is where it starts.
ALLOWED_MULTI_PRIMARY: dict[str, str] = {}

#: Classes exempt from the theme refresh forever, as ``"<module>:<Class>"``. The bar is
#: "this should never be fixed", not "not yet". Keep it empty if you can.
NEVER_FIX: dict[str, str] = {}


def _assert_clean(result) -> None:
    assert not result, result.message()
    assert not result.stale, f"stale allowlist entries: {result.stale}"


def test_every_themed_widget_refreshes_on_a_theme_change():
    _assert_clean(guards.theme_refresh_offenders(GUI_ROOT, NEVER_FIX))


def test_every_mixin_user_uses_it_correctly():
    assert guards.mixin_misuse(GUI_ROOT) == []


def test_no_literal_font_size():
    _assert_clean(guards.literal_font_sizes(PACKAGE))


def test_no_bare_push_button_but_the_allowed_ones():
    _assert_clean(guards.bare_buttons(GUI_ROOT, ALLOWED_PUSH_BUTTONS))


def test_at_most_one_primary_per_module():
    _assert_clean(guards.extra_primaries(GUI_ROOT, ALLOWED_MULTI_PRIMARY))


def test_no_qss_targets_a_compound_widget():
    _assert_clean(guards.compound_widget_qss(PACKAGE))


def test_no_coroutine_opens_a_modal_itself():
    _assert_clean(guards.modals_in_coroutines(PACKAGE))


def test_no_game_action_is_pressed_by_a_fixed_key():
    _assert_clean(fixed_key_presses(PACKAGE))


def test_no_module_is_a_copy_of_one_of_core_s():
    _assert_clean(core_copies(PACKAGE))
