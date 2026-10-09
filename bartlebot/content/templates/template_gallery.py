"""What the Templates dialog shows and allows, without the dialog (#164).

File › Templates… is where every user makes and changes templates of their own. It
lists every template, Official and Custom, one card each, and offers five verbs on
them. Which are allowed follows from where a template comes from:

  ========================  ============  =====================================
  verb                      Official      Custom
  ========================  ============  =====================================
  Edit (opens the draft)    dev mode only yes
  Duplicate                 yes           yes
  Rename                    no            yes
  Delete                    no            yes, naming the bots based on it
  New Template              blank, or from Duplicate
  ========================  ============  =====================================

Developer mode makes the two roots one directory, so every template there reads as
Custom and the maintainer edits the shipped ones in place, as before. What it cannot do
is delete one that has been released, since that folder is what ships
(:func:`bartlebot.content.templates.template_store.deletion_refused`), or rename one anywhere but in its
draft, which the next release carries
(:func:`bartlebot.content.templates.template_store.rename_waits_for_release`).

No Qt and no settings here: the dialog is ``gui/dialogs/templates_dialog.py``, and a test builds
a gallery on a ``tmp_path``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from bartlebot.bot.schema import BotConfig
from bartlebot.content.store_roots import Origin
from bartlebot.content.templates import template_store
from bartlebot.content.templates.template_store import Template

logger = logging.getLogger(__name__)

#: Why Edit is off on an Official template: the card's tooltip, and the controller's refusal.
OFFICIAL_EDIT_REFUSED = (
    "Official templates change only with an app update. Duplicate it to make a copy of your own to edit."
)


@dataclass(frozen=True)
class GalleryEntry:
    """One template as the dialog shows it: its card, and which verbs it allows."""

    template: Template
    #: What its card says under the name: the blurb's first paragraph. The whole blurb is the tooltip.
    summary: str
    blurb: str
    #: The tag whose medallion stands for it, as on New Bot's cards; ``None`` with no tags.
    icon_tag: str | None
    #: How many of the user's bots were made from it.
    bots: int
    #: The template it was duplicated from, and which version, as a phrase; "" for none.
    forked_from: str
    #: "" when Edit is allowed, else why not.
    edit_refused: str
    #: "" when Delete is allowed, else why not.
    delete_refused: str
    #: The name the draft carries when it differs from the card's, which its next release
    #: will carry (:func:`~bartlebot.content.templates.template_store.renamed_in_draft`); "" otherwise.
    renamed_to: str = ""

    @property
    def id(self) -> str:
        return self.template.id

    @property
    def name(self) -> str:
        return self.template.name

    @property
    def origin(self) -> Origin:
        return self.template.origin

    @property
    def may_edit(self) -> bool:
        return not self.edit_refused

    @property
    def may_duplicate(self) -> bool:
        """Anything released can be copied, and the user's own draft before its first release."""
        try:
            template_store.duplicate_source(self.template)
        except LookupError:
            return False
        return True

    @property
    def may_rename(self) -> bool:
        return self.origin is Origin.USER

    @property
    def may_delete(self) -> bool:
        return not self.delete_refused

    @property
    def detail(self) -> str:
        """The card's last line: latest version, the bots made from it, and where it was copied from."""
        latest = self.template.latest_version
        parts = [f"v{latest}" if latest is not None else "Not released yet"]
        parts.append(bots_phrase(self.bots))
        if self.forked_from:
            parts.append(f"from {self.forked_from}")
        if self.renamed_to:
            parts.append(f"renamed to {self.renamed_to} in draft")
        return " · ".join(parts)


def bots_phrase(count: int) -> str:
    """``no bots``, ``1 bot``, ``3 bots``: how many of the user's bots a template made."""
    if count == 0:
        return "no bots"
    return f"{count} bot" if count == 1 else f"{count} bots"


def _face(template: Template) -> BotConfig | None:
    """What a card is drawn from: the latest released version, or the draft before the first release."""
    latest = template.latest_version
    path = template.version_path(latest) if latest is not None else template.draft_path
    try:
        return BotConfig.load(path)
    except Exception:
        logger.warning("Template %s: %s does not load", template.name, path.name, exc_info=True)
        return None


def gallery(
    bots_dirs: tuple[str | Path, ...] = (),
    *,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
    dev: bool = False,
) -> list[GalleryEntry]:
    """Every template under both roots, by name, as the dialog lists them.

    ``bots_dirs`` are where the user's bots are, which the counts read. ``dev`` is
    developer mode, which allows Edit on an Official template; outside a test it is
    already the case that every template reads as Custom there.
    """
    templates = template_store.list_templates(root, user_root)
    names = {t.id: t.name for t in templates}
    counts = template_store.count_bots_based_on(*bots_dirs)
    entries = []
    for template in templates:
        face = _face(template)
        meta = face.template_meta if face is not None else None
        blurb = meta.blurb.strip() if meta is not None else ""
        source = meta.forked_from if meta is not None else None
        forked_from = ""
        if source is not None:
            forked_from = f"{names.get(source.id, 'a template no longer here')} v{source.version}"
        entries.append(
            GalleryEntry(
                template=template,
                summary=blurb.split("\n\n", 1)[0].strip(),
                blurb=blurb,
                icon_tag=face.tags[0] if face is not None and face.tags else None,
                bots=counts.get(template.id, 0),
                forked_from=forked_from,
                edit_refused="" if template.origin is Origin.USER or dev else OFFICIAL_EDIT_REFUSED,
                delete_refused=template_store.deletion_refused(template, root, user_root),
                renamed_to=template_store.renamed_in_draft(template) or "",
            )
        )
    return entries


def copy_name(name: str, taken: set[str] | list[str]) -> str:
    """What Duplicate offers to call a copy of ``name``: ``Boss Farmer (2)``, past the names taken.

    Compared without case, so two cards in New Bot never differ only by it.
    """
    lowered = {t.casefold() for t in taken}
    n = 2
    while f"{name} ({n})".casefold() in lowered:
        n += 1
    return f"{name} ({n})"
