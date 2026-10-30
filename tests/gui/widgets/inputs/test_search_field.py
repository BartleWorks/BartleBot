"""Every search in BartleBot's GUI uses core's search field.

The field itself, and its tests, moved to core's ``bartleby/qt/widgets/inputs/`` in Stage
11 of #167; core runs the same check over its own Qt tier.
"""

import re

from bartlebot.paths import project_root

GUI_DIR = project_root() / "bartlebot" / "gui"

_PLAIN_SEARCH = re.compile(
    r"QLineEdit\(\)\s*\n\s*\w[\w.]*\.setPlaceholderText\(\s*[\"'][^\"']*"
    r"(?:[Ss]earch|[Ff]ilter|[Ff]ind)",
)


def test_no_search_field_is_a_plain_line_edit():
    """A magnifier is how a free-text field says it narrows what is below, and it
    should not depend on whoever wrote the dialog remembering to add one."""
    offenders = []
    for path in sorted(GUI_DIR.rglob("*.py")):
        if _PLAIN_SEARCH.search(path.read_text(encoding="utf-8")):
            offenders.append(path.relative_to(GUI_DIR).as_posix())

    assert not offenders, f"search fields not using SearchLineEdit: {offenders}"
