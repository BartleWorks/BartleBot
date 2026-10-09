"""Static guard: the button vocabulary stays a vocabulary.

``ActionButton``, ``IconButton`` and ``LinkButton`` are the app's three button
classes, and which one a widget is is a decision with a documented reason behind
it (see ``gui/CLAUDE.md``, *Buttons*). None of that survives on its own. The two
guards this one joins exist because exactly this kind of convention drifted here
already:

    The sizes lived as literals in about eighty places before, which is why they
    had drifted into six values doing four jobs. — ``gui/CLAUDE.md``

Three checks, each guarding a different failure:

1. **No new bare ``QPushButton``** — the convention eroding one reasonable-looking
   exception at a time. The allowlist is the real content here: every entry names
   a widget and carries the reason it is not an ``ActionButton``, so the next
   person meets a documented decision rather than a judgement call.
2. **At most one ``PRIMARY`` per module** — the rule most likely to go first,
   because a second ``PRIMARY`` always looks locally reasonable.
3. **No QSS targeting a compound widget's subcontrols** — the one way a styling
   change breaks *rendering* rather than looks.

Parsed with ``ast``, not grepped. Several of these modules build stylesheets whose
text contains a ``QPushButton(``-shaped string, and a line-based regex cannot tell
a selector from a constructor.
"""

from __future__ import annotations

import ast
import functools
import pathlib
import re

from bartlebot.paths import project_root

GUI_ROOT = project_root() / "bartlebot" / "gui"

# ---------------------------------------------------------------------------
# Check 1 — bare QPushButton
# ---------------------------------------------------------------------------

#: Every bare ``QPushButton`` under ``gui/`` that is meant to stay one, keyed
#: ``"<module>:<the name it is bound to>"`` and carrying *why*.
#:
#: Keyed by name rather than by line, so an edit above an entry does not
#: invalidate it — and so the key says what the button is, which is the point.
#: The reason is printed when the check fails, so adding an entry means writing a
#: sentence the next person can act on.
#:
#: The bar is "this is not a labeled verb sharing a row". Two shapes qualify
#: today: a button whose *background is the data* it displays, and a button that
#: belongs to some other vocabulary (a status pill, a section-heading toggle).
#: "It would be fiddly to convert" is not a reason.
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

# ---------------------------------------------------------------------------
# Check 2 — one PRIMARY per surface
# ---------------------------------------------------------------------------

#: Modules allowed more than one ``ActionStyle.PRIMARY``, and why.
#:
#: Module is a proxy for surface, and where it genuinely is not — one module
#: holding two dialogs — the fix is an entry here naming both surfaces, not a
#: weaker check. Empty is the goal, and is where it starts.
ALLOWED_MULTI_PRIMARY: dict[str, str] = {}

#: The modules that *define* the weights rather than spend them. An occurrence of
#: ``ActionStyle.PRIMARY`` in ``action_button.py`` is a branch of the styling code,
#: and in ``dialog_footer.py`` a parameter default; neither is a button on a
#: surface, so counting them could only ever produce a false alarm.
VOCABULARY_MODULES = frozenset({"widgets/action_button.py", "widgets/dialog_footer.py"})

# ---------------------------------------------------------------------------
# Check 3 — QSS that takes a compound widget over
# ---------------------------------------------------------------------------

#: ``theme.py`` owns the global stylesheet and is the one place allowed to know
#: about any of this — it styles the combo box's drop-down and the scrollbars
#: deliberately, with the art to back them up.
QSS_OWNER = "theme.py"

#: Selectors that make ``QStyleSheetStyle`` take over a compound widget. Matched
#: against stylesheet *strings*, so an ``isinstance(w, QAbstractSpinBox)`` in
#: ordinary code is invisible here — correctly, since it styles nothing.
FORBIDDEN_SELECTORS: dict[str, str] = {
    "QAbstractSpinBox": (
        "theme.py: 'QAbstractSpinBox is deliberately absent. A border on it makes "
        "QStyleSheetStyle take over the whole control and the up/down buttons stop being "
        "drawn at all — the same takeover build_palette avoids for QPushButton.' "
        "Restoring them means writing ::up-button and ::down-arrow with our own art. "
        "Leave the stepper with the style's own frame."
    ),
    "QSpinBox": "Same takeover as QAbstractSpinBox — the steppers stop being drawn at all.",
    "QDoubleSpinBox": "Same takeover as QAbstractSpinBox — the steppers stop being drawn at all.",
    "QComboBox::drop-down": (
        "The arrow becomes ours to draw the moment this is set, and the theme's arrow is "
        "an SVG rendered per theme. Style the combo box in theme.py or not at all."
    ),
    "QCheckBox::indicator": "Qt stops drawing the check mark and the box, and the app has no art for either.",
    "QRadioButton::indicator": "Qt stops drawing the dot and the ring, and the app has no art for either.",
    "QScrollBar": (
        "One rule anywhere on a scrollbar hands the whole control over, arrows included. "
        "theme.py styles them globally, including the add-line/sub-line rules that hide "
        "those arrows on purpose."
    ),
}

#: A QSS comment is prose about a rule, not a rule — ``theme.py``'s own account of
#: the spinbox trap lives inside one.
_QSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


# ---------------------------------------------------------------------------
# Shared scaffolding
# ---------------------------------------------------------------------------


def _iter_gui_files() -> list[pathlib.Path]:
    return sorted(p for p in GUI_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


@functools.lru_cache(maxsize=1)
def _trees() -> tuple[tuple[str, ast.Module], ...]:
    """Every module under ``gui/``, as ("posix/relative/path.py", tree). Parsed once."""
    return tuple(
        (p.relative_to(GUI_ROOT).as_posix(), ast.parse(p.read_text(encoding="utf-8"))) for p in _iter_gui_files()
    )


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)


def _bound_name(stmt: ast.stmt) -> str | None:
    """The name a statement binds, for ``x = ...``, ``self._x = ...`` and annotated forms."""
    targets: list[ast.expr] = []
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
    elif isinstance(stmt, ast.AnnAssign):
        targets = [stmt.target]
    for t in targets:
        if isinstance(t, ast.Name):
            return t.id
        if isinstance(t, ast.Attribute):
            return t.attr
    return None


@functools.lru_cache(maxsize=1)
def _push_button_sites() -> tuple[tuple[str, str, int], ...]:
    """Every direct ``QPushButton(...)`` under ``gui/``, as (key, module, line).

    Direct instantiation only. ``IconButton`` and ``LinkButton`` both subclass
    ``QPushButton``, and they are the sanctioned answer for the buttons that are
    not labeled verbs — a check that caught subclasses would flag every correct
    conversion the rollout made.

    A call that is never bound to a name still gets a key, off its line number, so
    it cannot slip past by being written inline. That key moves when the file does,
    which is the nudge to give it a name if it is meant to be allowlisted.
    """
    named: dict[int, str] = {}
    out: list[tuple[str, str, int]] = []
    for module, tree in _trees():
        named.clear()
        for stmt in ast.walk(tree):
            if not isinstance(stmt, (ast.Assign, ast.AnnAssign)) or stmt.value is None:
                continue
            name = _bound_name(stmt)
            if name is None:
                continue
            for call in (n for n in ast.walk(stmt.value) if isinstance(n, ast.Call)):
                if _call_name(call) == "QPushButton":
                    named[call.lineno] = name
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            if _call_name(call) != "QPushButton":
                continue
            name = named.get(call.lineno, f"<line {call.lineno}>")
            out.append((f"{module}:{name}", module, call.lineno))
    return tuple(sorted(set(out)))


def _stylesheet_strings() -> list[tuple[str, int, str]]:
    """Every non-docstring string literal under ``gui/``, as (module, line, text).

    Docstrings are skipped because prose *about* a selector is not a rule using it —
    ``theme.py`` and this file both explain the spinbox trap by naming the class.
    """
    out: list[tuple[str, int, str]] = []
    for module, tree in _trees():
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstrings.add(id(first.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                out.append((module, node.lineno, node.value))
    return out


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def test_no_new_bare_push_buttons() -> None:
    """Every ``QPushButton`` under ``gui/`` is one of the three classes, or allowlisted.

    Fusion's default chrome is a beveled frame sized to its own label: it flattens
    the SVG icons drawn against it, and a row of such buttons sizes each one to its
    own text instead of sharing the row. That is what the three button classes
    exist to stop, and a bare ``QPushButton`` is how it comes back.
    """
    offenders = [(k, m, n) for k, m, n in _push_button_sites() if k not in ALLOWED_PUSH_BUTTONS]
    if not offenders:
        return

    lines = [
        f"{len(offenders)} bare QPushButton(s) under gui/ are not on the allowlist.",
        "",
        "Use ActionButton for a labeled verb sharing a row, IconButton for a glyph",
        "with no label, or LinkButton for a control inline in prose or under a card.",
        "See gui/CLAUDE.md, 'Buttons'.",
        "",
        "If it is genuinely none of those, add it to ALLOWED_PUSH_BUTTONS in this file",
        "with a reason — the reason is the deliverable, not the exemption.",
        "",
    ]
    lines += [f"  {key}  ({module}:{line})" for key, module, line in offenders]
    raise AssertionError("\n".join(lines))


def test_the_push_button_allowlist_has_no_stale_entries() -> None:
    """An allowlist that outlives its reason is how a guard rots into a no-op."""
    live = {key for key, _, _ in _push_button_sites()}
    stale = sorted(k for k in ALLOWED_PUSH_BUTTONS if k not in live)
    assert not stale, (
        f"ALLOWED_PUSH_BUTTONS names {len(stale)} button(s) that no longer exist: {stale}. "
        "Remove them — an exemption for something that is gone silences the check for "
        "that name if it ever comes back."
    )


def _primary_counts() -> dict[str, list[int]]:
    """Map module -> the lines mentioning ``ActionStyle.PRIMARY``."""
    counts: dict[str, list[int]] = {}
    for module, tree in _trees():
        if module in VOCABULARY_MODULES:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "PRIMARY"
                and isinstance(node.value, ast.Name)
                and node.value.id == "ActionStyle"
            ):
                counts.setdefault(module, []).append(node.lineno)
    return counts


def test_at_most_one_primary_per_module() -> None:
    """``PRIMARY`` is "the one thing this surface expects you to do" — so, one.

    A second one always looks locally reasonable, which is exactly why the rule
    needs a test rather than a paragraph. Module is the proxy for surface; where a
    module really does hold two surfaces, allowlist it naming both.

    Two things this deliberately does not catch. A ``dialog_footer()`` call builds a
    ``PRIMARY`` commit without naming the style, so a dialog that uses the footer
    *and* declares a ``PRIMARY`` of its own counts as one here — the stricter reading
    flags ``node_browser_dialog``, whose footer and whose Close button are mutually
    exclusive branches of one ``if``. And a ``SECONDARY`` with no ``PRIMARY`` beside it
    is legal: see :class:`ActionStyle`'s docstring, settled while writing this check.
    The weight means "a way forward that is not the commit", which a surface with no
    commit of its own can still have — ``assignment_card``'s *Save as Player
    Strategy…* and the canvas toolbar's *New Bot* are both that.
    """
    offenders = {
        m: lines for m, lines in _primary_counts().items() if len(lines) > 1 and m not in ALLOWED_MULTI_PRIMARY
    }
    if not offenders:
        return

    report = [
        "At most one ActionStyle.PRIMARY per surface (gui/CLAUDE.md, 'Buttons').",
        "A second filled button makes neither of them read as the expected one.",
        "",
        "Demote the others to SECONDARY (a way forward that is not the commit) or",
        "TERTIARY (everything else). If this module genuinely holds two surfaces,",
        "add it to ALLOWED_MULTI_PRIMARY with a reason naming both.",
        "",
    ]
    report += [f"  {module}: lines {', '.join(str(n) for n in lines)}" for module, lines in sorted(offenders.items())]
    raise AssertionError("\n".join(report))


def test_the_multi_primary_allowlist_has_no_stale_entries() -> None:
    counts = _primary_counts()
    stale = sorted(m for m in ALLOWED_MULTI_PRIMARY if len(counts.get(m, [])) <= 1)
    assert not stale, f"ALLOWED_MULTI_PRIMARY names modules with one PRIMARY or none: {stale}. Remove them."


def test_no_qss_targets_a_compound_widgets_subcontrols() -> None:
    """A stylesheet rule on a compound widget deletes the parts Qt was drawing.

    Setting any property on one of these hands the whole control to
    ``QStyleSheetStyle``, which then draws only what the stylesheet describes — so a
    spinbox loses its steppers, a check box its check mark, a scrollbar its arrows.
    Nothing fails and nothing logs; the parts are simply not there any more.

    Someone rolling out consistent button styling is the likeliest person to reach
    for a spinbox's stepper buttons, which is why this is guarded although nothing
    in the app does it today.
    """
    offenders: list[str] = []
    for module, line, text in _stylesheet_strings():
        if module == QSS_OWNER:
            continue
        body = _QSS_COMMENT.sub("", text)
        for selector, why in FORBIDDEN_SELECTORS.items():
            if selector in body:
                offenders.append(f"  {module}:{line}  targets {selector}\n      {why}")
    assert not offenders, (
        "QSS under gui/ targets a compound widget, whose subcontrols Qt then stops drawing.\n"
        f"Only {QSS_OWNER} may — it owns the global stylesheet and has the art to back it.\n\n" + "\n".join(offenders)
    )
