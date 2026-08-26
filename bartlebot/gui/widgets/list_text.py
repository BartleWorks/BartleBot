"""Comma-separated text ↔ ``list[str]``, for the param forms that edit string lists.

Both the ``str_list`` line edit and the quicksell keep-list picker store their
value as one line of comma-separated text. That is fine until an entry contains a
comma of its own — ``No Pain, No Gain Onyx`` is a real item name — at which point a
naive split silently turns one entry into two.

Splitting is therefore quote-aware, and formatting quotes an entry only when it
has to. Round-tripping is the property that matters: ``parse(format(xs)) == xs``
for any list of names.
"""

from __future__ import annotations

import csv


def parse_list_text(text: str) -> list[str]:
    """Split one line of comma-separated text into entries.

    Tolerates the shapes a user might type or paste: surrounding brackets, quoted
    entries, stray whitespace. Blank entries are dropped — in a keep list an empty
    keyword is a substring of every name and would keep the whole backpack.
    """
    text = text.strip().strip("[]").strip()
    if not text:
        return []
    try:
        fields = next(csv.reader([text], skipinitialspace=True))
    except (csv.Error, StopIteration):
        # A malformed line (an unbalanced quote, say) still has to yield something
        # rather than throw while the user is mid-keystroke.
        fields = text.split(",")
    # csv has already removed any double quotes it used for grouping, so only the
    # single quotes a user might type by hand are stripped here — doing both would
    # eat the closing quote of a name that legitimately ends in one.
    return [entry for entry in (f.strip().strip("'").strip() for f in fields) if entry]


def format_list_text(values: list[str]) -> str:
    """Render entries as one comma-separated line, quoting only where needed.

    Quoting is applied per entry rather than by ``csv.writer`` over the whole row,
    so the ", " separator can be spaced for readability without also spacing the
    commas *inside* a quoted name.
    """
    return ", ".join(_quoted(v) for v in values)


def _quoted(value: str) -> str:
    if "," in value or '"' in value:
        return '"' + value.replace('"', '""') + '"'
    return value
