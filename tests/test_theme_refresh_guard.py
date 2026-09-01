"""Static guard: every widget holding a THEME-derived value must re-derive it.

``QApplication.setStyleSheet`` does not recompute a stylesheet a widget set on
itself earlier, and ``svg_icon`` bakes its tint in at render time. So a value
built from ``THEME`` at construction is a frozen snapshot of whatever theme was
active then, and the widget shows stale colours until the app restarts. The fix
is one ``changeEvent`` override per widget (see CLAUDE.md, *GUI theming*).

That convention is documented, has a worked code sample, and is on the PR
checklist — and it was still missing from 22 of 43 themed classes when this test
was written. The convention was never the hard part; *noticing* was, because
noticing meant a human switching themes and eyeballing every panel in the app.
This is that human, on every commit.

Two things make it catch more than a grep would:

* **Helpers are transparent.** ``_apply_list_style(w)`` styles a widget it was
  handed, and ``field_label(text)`` returns one it built. Neither can re-style
  anything on a theme switch — the *caller* owns that instance and is the one
  that has to re-apply. So a themed helper propagates to its callers, to a fixed
  point, and the class that gets blamed is the class that can fix it.
* **A bare ``svg_icon(name)`` is themed.** Its ``tint`` defaults to
  ``THEME.icon_tint``, so an icon set once and kept goes stale exactly like a
  stylesheet — but with no stylesheet anywhere for a text search to find.

Widgets that read ``THEME`` at paint time need none of this and are invisible
here: they hold no baked value, which is why ``_RowWidget`` and the region items
paint in a ``paintEvent`` instead.
"""

from __future__ import annotations

import ast
import functools
import pathlib

GUI_ROOT = pathlib.Path(__file__).resolve().parent.parent / "bartlebot" / "gui"

# A call is "themed" when its argument mentions one of these. Two are indirect:
# pill_style and group_accent resolve THEME internally, so the call site names
# neither THEME nor a colour.
THEME_MARKERS = ("THEME.", "pill_style(", "group_accent(", "region_color(")

# Inheriting this is accepted in place of a hand-written changeEvent.
THEMED_MIXIN = "ThemedMixin"

# Classes exempt forever. The bar is "this should NEVER be fixed" — not "not yet",
# not "harmless today". A widget that is safe only because of some *other* fact
# about the app does not qualify: every QDialog here is currently unfalsifiable
# because the settings dialog is modal and applies the theme only after it closes,
# but making settings non-modal or adding a live theme preview would break all of
# them at once, silently. That is a deferred fix, not an exemption.
#
# Map of "Module.ClassName" -> why. Keep it empty if you can.
NEVER_FIX: dict[str, str] = {}


def _iter_gui_files() -> list[pathlib.Path]:
    return sorted(p for p in GUI_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _called_names(node: ast.AST) -> set[str]:
    """Every function/method name called anywhere inside *node*."""
    out = set()
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        f = call.func
        out.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None))
    return out - {None}


def _direct_themed(node: ast.AST) -> str | None:
    """Name the way this call bakes in a THEME-derived value, or None."""
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)

    if name == "setStyleSheet" and any(m in ast.unparse(node) for m in THEME_MARKERS):
        return "setStyleSheet(THEME...)"

    if name == "svg_icon":
        tint = next((k.value for k in node.keywords if k.arg == "tint"), None)
        if tint is None:
            # Omitted tint is not "no tint" — svg_icon falls back to THEME.icon_tint.
            return "svg_icon(tint defaults to THEME.icon_tint)"
        if any(m in ast.unparse(tint) for m in THEME_MARKERS):
            return "svg_icon(tint=THEME...)"
        # An explicit literal tint, or tint="", is theme-independent by choice.
    return None


def _themed_helpers(trees: dict[pathlib.Path, ast.Module]) -> set[str]:
    """Module-level functions that bake in a THEME value, directly or via another.

    Matched by bare name across the package rather than resolved through imports:
    these are private helpers with distinctive names, and a name collision would
    only ever over-report, which fails loudly instead of silently.
    """
    helpers = {n.name: n for t in trees.values() for n in t.body if isinstance(n, ast.FunctionDef)}
    themed = {name for name, fn in helpers.items() if any(_direct_themed(s) for s in ast.walk(fn))}
    while True:
        grew = {n for n, fn in helpers.items() if n not in themed and (_called_names(fn) & themed)}
        if not grew:
            return themed
        themed |= grew


def _handles_theme_change(cls: ast.ClassDef) -> bool:
    """True if this class re-derives its styling when the theme switches.

    A ``changeEvent`` that never mentions ``StyleChange`` does not count — an
    empty override is how a half-finished fix looks, and it would otherwise
    silence this check for the class forever.
    """
    if any(getattr(b, "id", getattr(b, "attr", None)) == THEMED_MIXIN for b in cls.bases):
        return True
    return any(
        isinstance(s, ast.FunctionDef) and s.name == "changeEvent" and "StyleChange" in ast.unparse(s)
        for s in ast.walk(cls)
    )


@functools.lru_cache(maxsize=1)
def _violations_cached() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Map "module:ClassName" -> the themed things it does without re-deriving them."""
    trees = {p: ast.parse(p.read_text(encoding="utf-8")) for p in _iter_gui_files()}
    helpers = _themed_helpers(trees)

    found: dict[str, list[str]] = {}
    for path, tree in trees.items():
        module = path.relative_to(GUI_ROOT).as_posix()
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            reasons = sorted({r for s in ast.walk(cls) if (r := _direct_themed(s))})
            reasons += [f"calls {h}()" for h in sorted(_called_names(cls) & helpers)]
            if reasons and not _handles_theme_change(cls):
                found[f"{module}:{cls.name}"] = reasons
    return tuple((k, tuple(v)) for k, v in sorted(found.items()))


def _violations() -> dict[str, list[str]]:
    """Parsing the package is ~1 s, and both tests need the same answer."""
    return {k: list(v) for k, v in _violations_cached()}


def test_every_themed_widget_refreshes_on_theme_change():
    """A widget that bakes in a THEME value must re-derive it on StyleChange."""
    offenders = {k: v for k, v in _violations().items() if k not in NEVER_FIX}
    if not offenders:
        return

    lines = [
        f"{len(offenders)} class(es) bake in a THEME-derived value but never re-derive it.",
        "They will show stale colours until the app is restarted.",
        "",
        "Fix: inherit ThemedMixin and implement apply_theme_styles(), or override",
        "changeEvent to re-apply on QEvent.Type.StyleChange. See CLAUDE.md, 'GUI theming'.",
        "",
    ]
    for name, reasons in sorted(offenders.items()):
        lines.append(f"  {name}")
        lines.extend(f"      - {r}" for r in reasons)
    raise AssertionError("\n".join(lines))


def test_never_fix_allowlist_has_no_stale_entries():
    """An allowlist that outlives its reason is how a guard test rots into a no-op."""
    live = _violations()
    stale = sorted(k for k in NEVER_FIX if k not in live)
    assert not stale, (
        f"NEVER_FIX names {len(stale)} class(es) that no longer trip the check: {stale}. "
        "Remove them — a permanent exemption for a problem that is gone silences the "
        "check for that class if the problem ever comes back."
    )
