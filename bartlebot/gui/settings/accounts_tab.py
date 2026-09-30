"""Settings tab: Game (accounts, launch profiles, and game embed resolution).

Also the home of the *card actions* — the module-level ``add_``/``edit_``/``delete_``/
``duplicate_`` functions below, which are what every surface that manages an account or
a launch profile calls. They live here because the editors they wrap do, not
because the tab owns them: none of them needs an ``AccountsTab`` to exist, which is the
whole point. The launch screen's cards reached the profile editor by building the entire
Settings dialog underneath it until the profile-editor dialog work, and the Add cards and the Game tab's own
buttons would each have grown a second copy of the delete cascade otherwise.

Each returns whether anything was written, which is every caller's cue to refresh
whatever it draws accounts or profiles with.

Each also *saves*. That is not incidental: the one ``save()`` these used to reach was
``SettingsDialog._on_accept``, and a card on the launch screen has no Settings dialog
behind it, so an account added there survived until the process exited and no further --
and the tab's own ``apply()`` has always claimed it "saves immediately on each action",
which was true of the reorders alone. The write itself is now the shared account store's
(``shared.account_store``), which persists on every set; these functions call it once per
action rather than mutating a list in place.

Every one of them runs ``exec()`` on a dialog or a message box, so every one must be
called from a synchronous Qt slot: Python 3.13 forbids re-entering Qt's event loop from
inside a running asyncio task. See the qasync section of CLAUDE.md.

The account editor is here, next to the tab that owns account records. The *profile*
editor is not: it draws the same cards in the same column as this tab does, so it lives
in ``profile_editor`` beside nothing but them, and this module imports it rather than the
other way round. ``mark_unfittable_resolutions`` went with it, being the one thing the
two resolution combo boxes share.
"""

from __future__ import annotations

from copy import deepcopy

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot import character_roster
from bartlebot.credentials import CredentialStore
from bartlebot.gui.account_accent import ACCOUNT_PALETTE_SIZE, account_accent, account_color_keys, pinned_color_key
from bartlebot.gui.game_embed.card_columns import (
    EMPTY_ACCOUNTS_HINT,
    EMPTY_PROFILES_HINT,
    CardColumn,
    apply_order,
    refresh_column_fit,
)
from bartlebot.gui.game_embed.launch_cards import AccountCard, CardMode, ProfileCard
from bartlebot.gui.settings.profile_editor import ProfileDialog, mark_unfittable_resolutions
from bartlebot.gui.settings.rows import hint
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.dialog_footer import dialog_footer
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import VALID_RESOLUTIONS, SettingsManager


def _slots_in_use(exclude: str = "") -> dict[int, list[str]]:
    """Which palette slot each *other* account currently sits in, and who is in it.

    Resolved through :func:`account_color_keys` rather than read off the records, because
    a slot an account merely derived is just as taken on screen as one it pinned — and it
    is the screen the picker is describing.

    *exclude* drops the account being edited, whose own slot is shown as the selection
    instead. Names come back through ``display_name``, so Streamer Mode censors them here
    like everywhere else.
    """
    accounts = [a for a in SettingsManager.instance().accounts if a.get("username") != exclude]
    keys = account_color_keys(accounts)
    holders: dict[int, list[str]] = {}
    for account in accounts:
        username = account.get("username", "")
        slot = keys.get(username)
        if slot is not None:
            holders.setdefault(slot, []).append(CredentialStore.display_name(username, account.get("alias") or None))
    return holders


class _Swatch(QToolButton):
    """One palette color, with a dot when another account already holds that slot.

    The dot is *painted* rather than set as the button's text. A "•" glyph sits on the
    text baseline and is drawn around its own x-height, neither of which is the middle of
    a 22px circle — at this size the offset is a pixel or two and reads as a mistake.
    Painting reads ``THEME`` at paint time, so this needs no theme-refresh plumbing of its
    own; its owner styles the fill and the ring.
    """

    #: Diameter of the marker, in px. Big enough to see against eight hues, small enough
    #: not to read as a second color inside the swatch.
    DOT_SIZE = 6

    def __init__(self, taken: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._taken = taken

    @property
    def taken(self) -> bool:
        """Whether this slot is drawn as already held by another account."""
        return self._taken

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._taken:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(THEME.pill_text))
        # QRectF, not the widget's QRect: an integer rect's center() floors to half a
        # pixel above and left of true center, which is the bug this replaced.
        radius = self.DOT_SIZE / 2
        painter.drawEllipse(QRectF(self.rect()).center(), radius, radius)


class _AccountColorPicker(ThemedMixin, QWidget):
    """Automatic, then one swatch per palette slot — the color row in ``_AccountDialog``.

    Two things about it are the point rather than the decoration:

    * **Automatic stays selectable, and is the default.** It clears the pin rather than
      writing a value. Without it a user who picks once could never get a derived color
      back, and the de-duplication in ``account_color_keys`` — the thing that keeps a
      fleet's colors distinct as it grows — would stop applying to that account forever.
    * **A slot another account holds is marked, not disabled.** Independent choices
      colliding is the bug ``account_accent`` was written to fix, so a picker that hid the
      current assignment would invite the user to reproduce it by hand. Two accounts
      sharing a color is still their call to make.

    The whole appearance is derived from the theme and the selection, so
    :meth:`apply_theme_styles` paints both and :meth:`set_selected` just asks for a
    repaint. That keeps the theme hook complete by construction rather than by care.
    """

    #: Side of a swatch, in px. Large enough to read the color and the taken marker at
    #: once, small enough that eight of them plus Automatic fit the dialog's 300px floor.
    SWATCH_SIZE = 22

    def __init__(self, selected: int | None = None, taken: dict[int, list[str]] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._taken = dict(taken or {})
        self._selected = selected if selected in range(1, ACCOUNT_PALETTE_SIZE + 1) else None

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._auto_btn = QToolButton()
        self._auto_btn.setText("Automatic")
        self._auto_btn.setCheckable(True)
        self._auto_btn.setToolTip("Choose a color from the username, kept distinct from the other accounts.")
        self._auto_btn.clicked.connect(lambda _=False: self.set_selected(None))
        row.addWidget(self._auto_btn)

        self._swatches: dict[int, _Swatch] = {}
        for key in range(1, ACCOUNT_PALETTE_SIZE + 1):
            holders = self._taken.get(key) or []
            btn = _Swatch(taken=bool(holders))
            btn.setCheckable(True)
            btn.setFixedSize(self.SWATCH_SIZE, self.SWATCH_SIZE)
            btn.setToolTip("Already used by " + ", ".join(holders) if holders else "Not used by another account")
            btn.clicked.connect(lambda _=False, k=key: self.set_selected(k))
            self._swatches[key] = btn
            row.addWidget(btn)
        row.addStretch()

        self.refresh_theme_styles()

    @property
    def selected(self) -> int | None:
        """The pinned slot, or None for Automatic — which is what gets stored."""
        return self._selected

    @property
    def swatches(self) -> dict[int, _Swatch]:
        """The color buttons, by palette slot. Read-only view for callers and tests."""
        return dict(self._swatches)

    @property
    def automatic_button(self) -> QToolButton:
        return self._auto_btn

    def set_selected(self, key: int | None) -> None:
        """Select *key*, or Automatic for None or anything outside the palette."""
        self._selected = key if key in self._swatches else None
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self._auto_btn.setChecked(self._selected is None)
        # The selection ring is THEME.text rather than THEME.accent: the accent is a blue,
        # and slot 1 is a blue, so an accent ring is the one selection a user cannot see.
        border = THEME.text if self._selected is None else THEME.border
        self._auto_btn.setStyleSheet(
            f"QToolButton {{ background: {THEME.surface}; color: {THEME.text}; "
            f"border: 2px solid {border}; border-radius: 4px; padding: 2px 8px; }}"
            f"QToolButton:hover {{ border-color: {THEME.accent}; }}"
        )
        for key, btn in self._swatches.items():
            btn.setChecked(key == self._selected)
            edge = THEME.text if key == self._selected else THEME.border
            radius = self.SWATCH_SIZE // 2
            btn.setStyleSheet(
                f"QToolButton {{ background: {account_accent(key)}; "
                f"border: 2px solid {edge}; border-radius: {radius}px; }}"
                f"QToolButton:hover {{ border-color: {THEME.accent}; }}"
            )


class _AccountDialog(QDialog):
    """Small dialog for adding or editing an account.

    A *username* opens it in edit mode, which differs in two ways: the password may be
    left blank to keep the stored one, and the username itself is read-only. The username
    is this account's key everywhere — the Credential Manager entry, the character roster
    file, and every launch profile's membership list — so it is not something an alias
    field's neighbor can quietly change. It used to be editable and silently ignored,
    which is the worse of the two: the user types a correction, presses OK, and the list
    comes back unchanged with nothing saying why. The alias is the field for what the
    account is *called*.

    The color row is here for the same reason: a pinned color is a property of
    the account record, and this is where a record is edited.
    """

    def __init__(
        self,
        username: str = "",
        alias: str = "",
        color: int | None = None,
        taken: dict[int, list[str]] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Account")
        self.setMinimumWidth(300)

        form = QFormLayout()
        self._username_edit = QLineEdit(username)
        self._alias_edit = QLineEdit(alias)
        self._alias_edit.setPlaceholderText("Optional — shown instead of username")
        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if username:
            self._username_edit.setReadOnly(True)
            self._username_edit.setToolTip("An account's username cannot be changed. Set an alias instead.")
            self._password_edit.setPlaceholderText("Leave blank to keep existing password")

        self._color_picker = _AccountColorPicker(selected=color, taken=taken)

        form.addRow("Username:", self._username_edit)
        form.addRow("Alias:", self._alias_edit)
        form.addRow("Password:", self._password_edit)
        form.addRow("Color:", self._color_picker)
        color_hint = ThemedTextLabel(
            "Shown on this account's card and on the profile pills that name it. "
            "A dot marks a color another account already uses.",
            css=f"font-size: {THEME.font_small_px}px;",
        )
        color_hint.setWordWrap(True)
        form.addRow("", color_hint)

        footer = dialog_footer(self)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(footer.layout)

    @property
    def username(self) -> str:
        return self._username_edit.text().strip()

    @property
    def alias(self) -> str:
        return self._alias_edit.text().strip()

    @property
    def password(self) -> str:
        return self._password_edit.text()

    @property
    def color(self) -> int | None:
        """The pinned palette slot, or None for Automatic — which stores no key at all."""
        return self._color_picker.selected

    @property
    def color_picker(self) -> _AccountColorPicker:
        return self._color_picker


def add_account(parent: QWidget | None = None) -> bool:
    """Open an empty account editor; store the credential and the record if accepted.

    A module function for the same reason as ``edit_launch_profile``: the Add affordance
    is a card at the foot of the launch screen's Accounts column as well as a
    button under the Game tab's list, and the card must not have to build a Settings
    dialog to reach the one editor it wants.

    Returns whether an account was written, which is every caller's cue to redraw
    whatever it draws accounts with. False covers all four ways nothing was saved: the
    editor cancelled, an empty username, an empty password, and a duplicate.
    """
    dlg = _AccountDialog(taken=_slots_in_use(), parent=parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    username = dlg.username
    if not username:
        QMessageBox.warning(parent, "Invalid", "Username cannot be empty.")
        return False
    if not dlg.password:
        QMessageBox.warning(parent, "Invalid", "Password cannot be empty for a new account.")
        return False
    accounts = SettingsManager.instance().accounts
    if any(a["username"] == username for a in accounts):
        QMessageBox.warning(parent, "Duplicate", f"Account '{username}' already exists.")
        return False
    CredentialStore.store(username, dlg.password)
    record = {"username": username, "alias": dlg.alias or ""}
    if dlg.color is not None:
        record["color"] = dlg.color
    accounts.append(record)
    SettingsManager.instance().accounts = accounts
    return True


def edit_account(username: str, parent: QWidget | None = None) -> bool:
    """Open the editor for the account *username*; store what it returns.

    The password is optional here and only written when one was typed: the field is blank
    on open, and an empty one means "keep the stored credential" rather than "clear it".
    Reading a password back out of the Credential Manager to prefill the field is what
    that alternative would cost.

    Returns whether anything was written. False covers no such account and a cancelled
    editor -- there is nothing else to refuse, since the username cannot change and an
    alias may legitimately be blank.

    Automatic *removes* the ``color`` key rather than writing a sentinel, which is what
    puts the account back under ``account_color_keys``' de-duplication. A key left
    behind holding None would read as "pins nothing usable" today, but it is a stored
    value that has to keep meaning that.
    """
    account = next((a for a in SettingsManager.instance().accounts if a.get("username") == username), None)
    if account is None:
        return False
    dlg = _AccountDialog(
        username=username,
        alias=account.get("alias") or "",
        color=pinned_color_key(account),
        taken=_slots_in_use(exclude=username),
        parent=parent,
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    if dlg.password:
        CredentialStore.store(username, dlg.password)
    accounts = SettingsManager.instance().accounts
    for a in accounts:
        if a.get("username") == username:
            a["alias"] = dlg.alias
            if dlg.color is None:
                a.pop("color", None)
            else:
                a["color"] = dlg.color
            break
    SettingsManager.instance().accounts = accounts
    return True


def delete_account(username: str, parent: QWidget | None = None) -> bool:
    """Confirm, then remove the account *username* and everything keyed to it.

    Four things go, and the confirm names three of them because they are not recoverable
    by adding the account back: the record, its password in the Credential Manager, and
    its character roster. The fourth is the cascade -- the username is stripped from every
    launch profile that launches it, because a profile naming an account that no longer
    exists is a launch that fails at the login screen with nothing on the card saying why.

    A roster is wizard names, which is exactly the data ``character_roster`` is careful
    about; leaving one behind for an account the user has just removed is the case it
    exists for.

    Returns whether anything was written. False covers no such account and a declined
    confirm.
    """
    account = next((a for a in SettingsManager.instance().accounts if a.get("username") == username), None)
    if account is None:
        return False
    display = CredentialStore.display_name(username, account.get("alias") or None)
    reply = QMessageBox.question(
        parent,
        "Delete account",
        f"Remove '{display}', its stored password, and its known characters?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return False
    CredentialStore.delete(username)
    character_roster.forget(username)
    store = SettingsManager.instance().account_store
    # Both lists in one write. Saving the accounts first would leave a window -- an
    # interrupted delete, or a second process reading between the two -- in which the
    # file holds profiles naming an account it no longer has.
    store.set_accounts([a for a in store.accounts if a.get("username") != username], save=False)
    profiles = store.launch_profiles
    for p in profiles:
        p["accounts"] = [u for u in p.get("accounts", []) if u != username]
    store.set_launch_profiles(profiles)
    return True


def reorder_accounts(order: list[int]) -> bool:
    """Rearrange the stored accounts into *order*. Whether anything was written.

    Order is a real setting and not a display preference: it is what the Accounts column
    and the Launch menu list, and a launch profile's own account order decides which
    window becomes Player 1.
    """
    sm = SettingsManager.instance()
    accounts = apply_order(sm.accounts, order)
    if accounts is None:
        return False
    sm.accounts = accounts
    return True


def reorder_launch_profiles(order: list[int]) -> bool:
    """Rearrange the stored launch profiles into *order*. Whether anything was written."""
    sm = SettingsManager.instance()
    profiles = apply_order(sm.launch_profiles, order)
    if profiles is None:
        return False
    sm.launch_profiles = profiles
    return True


def add_launch_profile(
    parent: QWidget | None = None,
    live_game_area: tuple[int, int] | None = None,
) -> bool:
    """Open an empty profile editor; save the profile if accepted.

    The counterpart to :func:`add_account`, and lifted out of ``AccountsTab`` for the
    same reason -- see that function, and ``edit_launch_profile`` below for what
    *live_game_area* is for.

    Returns whether a profile was written. False covers the three ways nothing was:
    the editor cancelled, an empty name, and a duplicate.
    """
    dlg = ProfileDialog(parent=parent, live_game_area=live_game_area)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    name = dlg.name
    if not name:
        QMessageBox.warning(parent, "Invalid", "Profile name cannot be empty.")
        return False
    profiles = SettingsManager.instance().launch_profiles
    if any(p["name"] == name for p in profiles):
        QMessageBox.warning(parent, "Duplicate", f"Profile '{name}' already exists.")
        return False
    profile: dict = {"name": name, "accounts": dlg.selected_usernames}
    if dlg.resolution is not None:
        profile["resolution"] = list(dlg.resolution)
    profiles.append(profile)
    SettingsManager.instance().launch_profiles = profiles
    return True


def edit_launch_profile(
    old_name: str,
    parent: QWidget | None = None,
    live_game_area: tuple[int, int] | None = None,
) -> bool:
    """Open the editor for the launch profile named *old_name*; save it if accepted.

    A module function rather than a method because the two places that edit a profile —
    the Game tab's Edit button and the gear on a launch card — have nothing else in
    common, and routing the card through the tab meant building the whole Settings dialog
    to reach one editor. What the card needs is the editor and the save; what it
    does not need is a second OK/Cancel around them, applying to every tab.

    Returns whether the profile was actually written, which is the caller's cue to
    refresh whatever it draws profiles with. False covers all three ways nothing was
    saved: no such profile, the editor cancelled, and a name the save refused.

    *live_game_area* is passed through so the editor's resolution picker judges a launch
    the same way the surface behind it does — see gui/screen_fit.budget_for.
    """
    profile = next((p for p in SettingsManager.instance().launch_profiles if p.get("name") == old_name), None)
    if profile is None:
        return False
    raw_res = profile.get("resolution")
    existing_res = tuple(raw_res) if raw_res else None
    dlg = ProfileDialog(
        name=old_name,
        selected_usernames=profile.get("accounts", []),
        resolution=existing_res,
        parent=parent,
        live_game_area=live_game_area,
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    new_name = dlg.name
    if not new_name:
        QMessageBox.warning(parent, "Invalid", "Profile name cannot be empty.")
        return False
    profiles = SettingsManager.instance().launch_profiles
    if new_name != old_name and any(p["name"] == new_name for p in profiles):
        QMessageBox.warning(parent, "Duplicate", f"Profile '{new_name}' already exists.")
        return False
    for p in profiles:
        if p["name"] == old_name:
            p["name"] = new_name
            p["accounts"] = dlg.selected_usernames
            if dlg.resolution is not None:
                p["resolution"] = list(dlg.resolution)
            else:
                p.pop("resolution", None)
            break
    SettingsManager.instance().launch_profiles = profiles
    return True


def _free_profile_name(base: str, taken: set[str]) -> str:
    """The first of "Foo (copy)", "Foo (copy 2)", "Foo (copy 3)"... that *taken* lacks.

    Counting from 2 on the second copy rather than 1 on the first, so duplicating once
    reads "Farm Team (copy)" and not "Farm Team (copy 1)" -- the numberless form is the
    common case and should be the one that looks unremarkable.

    Split out and given the taken set rather than reading settings, because what makes a
    name free is a question with an answer a test can ask directly.
    """
    candidate = f"{base} (copy)"
    n = 2
    while candidate in taken:
        candidate = f"{base} (copy {n})"
        n += 1
    return candidate


def duplicate_launch_profile(name: str) -> bool:
    """Copy the profile *name* under a free name, directly after the original.

    No dialog, and so no *parent*: there is nothing to ask. A duplicate is the cheap way
    to build the profile that is this one plus an account, or this one at a smaller
    resolution, and putting an editor in front of it would make it as expensive as
    starting from Add.

    Inserted after the original rather than appended, so the copy lands where the user is
    looking. Appending puts it at the foot of a column they may have to scroll to find,
    beside an Add card, which reads as something that was created somewhere else.

    Deep-copied. A shallow one would share the ``accounts`` list with the original, so
    the first edit to the copy's membership would silently rewrite the profile it came
    from -- and both would then be written back by the same save.

    Returns whether anything was written; False only if no profile has that name.
    """
    profiles = SettingsManager.instance().launch_profiles
    index = next((i for i, p in enumerate(profiles) if p.get("name") == name), None)
    if index is None:
        return False
    copy = deepcopy(profiles[index])
    copy["name"] = _free_profile_name(name, {p.get("name", "") for p in profiles})
    profiles.insert(index + 1, copy)
    SettingsManager.instance().launch_profiles = profiles
    return True


def delete_launch_profile(name: str, parent: QWidget | None = None) -> bool:
    """Confirm, then remove the launch profile *name*.

    The confirm is lighter than ``delete_account``'s on purpose, and says only what goes:
    a profile is a list of accounts and a resolution, so deleting one destroys no
    credential and no roster, and rebuilding it is a minute in the editor.

    Returns whether anything was written. False covers no such profile and a declined
    confirm.
    """
    profiles = SettingsManager.instance().launch_profiles
    if not any(p.get("name") == name for p in profiles):
        return False
    reply = QMessageBox.question(
        parent,
        "Delete profile",
        f"Remove launch profile '{name}'?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return False
    SettingsManager.instance().launch_profiles = [p for p in profiles if p.get("name") != name]
    return True


class AccountsTab(QWidget):
    """The Game tab: the default resolution, then the same two card columns as the launch screen.

    It used to draw the identical two lists as ``QListWidget`` rows of formatted text — an
    account was a display string, a profile was ``f"{name} ({n} accounts, WxH)"`` — so the
    surface where accounts are *edited* said strictly less about them than the surface
    where they are merely launched, and neither taught you to read the other. They
    are the same cards now, out of the same ``CardColumn``.

    What differs is the verbs, and they differ because this is the editing surface: a body
    click edits rather than launches, the pencil and trash sit on the card face rather
    than behind hover, and a card that will not fit is noted rather than grayed out —
    graying out the row the user came here to fix is hostile. All three ride on the cards'
    ``manage`` flag; see ``launch_cards``.

    So there are no Add / Edit / Delete button rows and no selection: selection existed
    only to feed those buttons, the verbs are on the cards, and Add is the card at the
    foot of each column.
    """

    def __init__(self, parent=None, live_game_area: tuple[int, int] | None = None) -> None:
        super().__init__(parent)
        # Set before the resolution section is built: its own fit pass reads it.
        self._live_game_area = live_game_area
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ------------------------------------------------------------------
        # What every launch does: the resolution, and the two options the app sets
        # in the client on its way up.
        # ------------------------------------------------------------------
        layout.addWidget(RuleHeading("On launch"))

        res_form = QFormLayout()
        res_form.setSpacing(8)
        self._res_combo = QComboBox()
        for w, h in VALID_RESOLUTIONS:
            self._res_combo.addItem(f"{w}×{h}", userData=(w, h))

        current = SettingsManager.instance().game_resolution
        for i in range(self._res_combo.count()):
            if self._res_combo.itemData(i) == current:
                self._res_combo.setCurrentIndex(i)
                break

        res_form.addRow("Default game resolution:", self._res_combo)
        # What the user has to do, and nothing they do not. This used to open by telling
        # them to set the resolution by hand in the game's own options, which has not been
        # true since BartleBot started writing preferences.xml before every launch —
        # instructions for work the app already does read as work still outstanding.
        # The Windowed mode sentence went the same way in the resolution-restore work, which now forces
        # IsFullscreen off at launch and restores it after.
        res_form.addRow(
            hint(
                "BartleBot sets the resolution itself at every launch and runs the game "
                "windowed, and at smaller sizes scales the game's own "
                "UI down to match while it runs, putting it back when the client closes. "
                "Widescreen sizes keep a larger UI than 4:3 ones of the same height."
            )
        )

        # Both of these are done to a client on its way up, which is why they are on this
        # tab and not the Bot one: neither is a level of any bot option's resolution, and
        # a bot that never starts still gets both.
        self._auto_select_cb = QCheckBox()
        self._auto_select_cb.setChecked(SettingsManager.instance().auto_select_character)
        self._auto_select_cb.setToolTip("Also used to bring a player back after it crashes mid-run.")
        res_form.addRow("Automatically select a character:", self._auto_select_cb)
        res_form.addRow(
            hint(
                "After a launched client logs in, press Play on the most recently played "
                "character. With this off every launched window still arrives at its "
                "character-select screen, embedded in the game view, waiting for you to pick."
            )
        )

        self._quick_tp_cb = QCheckBox()
        self._quick_tp_cb.setChecked(SettingsManager.instance().disable_quick_friend_teleport)
        res_form.addRow("Disable quick friend teleport:", self._quick_tp_cb)
        res_form.addRow(
            hint(
                "Turn the game's quick friend teleport option off in every client this app "
                "launches. With it on, nodes that double-click a Friends List name to open a "
                "profile teleport instead. The game reads this at startup, so it does not reach "
                "a client that is already running."
            )
        )

        layout.addLayout(res_form)

        self._res_combo.currentIndexChanged.connect(self._on_resolution_changed)
        self._refresh_resolution_fit()

        # ------------------------------------------------------------------
        # The two columns
        #
        # No heading over these: each column carries its own, and a third line
        # naming them above two that already do is the noise the rule was meant
        # to cut.
        # ------------------------------------------------------------------
        layout.addWidget(QLabel("Passwords are stored in Windows Credential Manager:"))

        columns = QHBoxLayout()
        columns.setSpacing(16)
        layout.addLayout(columns, 1)

        # No "Change resolution" link on either banner: the combo it would point at is
        # forty pixels above them.
        self._accounts_col = CardColumn("Accounts", EMPTY_ACCOUNTS_HINT, "Add account", show_settings_link=False)
        self._accounts_col.add_requested.connect(self._on_add)
        self._accounts_col.reordered.connect(self._on_accounts_reordered)
        columns.addWidget(self._accounts_col, 1)

        self._profiles_col = CardColumn(
            "Launch Profiles", EMPTY_PROFILES_HINT, "Add launch profile", show_settings_link=False
        )
        self._profiles_col.add_requested.connect(self._on_profile_add)
        self._profiles_col.reordered.connect(self._on_profiles_reordered)
        columns.addWidget(self._profiles_col, 1)

        self._refresh()

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def _on_resolution_changed(self) -> None:
        resolution = self._res_combo.currentData()
        if resolution:
            SettingsManager.instance().game_resolution = resolution
            SettingsManager.instance().save()
            # The account column's banner is a statement about this value, so it is stale
            # the moment the combo moves — and it is the banner that sent the user here.
            self._refresh_fit()

    def _refresh_resolution_fit(self) -> None:
        """Gray out the resolutions a *single* client could not fit at.

        One client, deliberately, even though this setting is also what a profile without
        its own resolution launches at. Sizing it against the largest such profile is
        defensible and was what this did, but it grays out most of the list for anyone who
        happens to keep a four-account profile around — and a user running several clients
        is a user who has a launch profile, which carries its own resolution combo sized
        against its own accounts. Judging the global default by the rarer case made the
        common one look broken.

        Only a hint either way, not a gate: this value is set for more than a running
        fleet, and the launch surfaces refuse an unfittable launch on their own — a profile
        that inherits a resolution too large for its account count is still stopped there,
        with a reason naming both numbers.
        """
        mark_unfittable_resolutions(self._res_combo, 1, self, self._live_game_area)

    # ------------------------------------------------------------------
    # The columns
    # ------------------------------------------------------------------

    @property
    def accounts_column(self) -> CardColumn:
        """The Accounts column. Public because a test has to reach the cards in it."""
        return self._accounts_col

    @property
    def profiles_column(self) -> CardColumn:
        """The Launch Profiles column."""
        return self._profiles_col

    def _refresh(self) -> None:
        """Rebuild both columns from settings, and re-judge the resolution list with them.

        Both, always. Every verb here reaches further than the column it was run from: a
        deleted account leaves every profile that launched it, an edited alias is a pill on
        each of those cards, and a reorder of either list changes what the Launch menu
        offers. Rebuilding one column and not the other is how a card ends up naming an
        account that no longer exists.
        """
        self._refresh_resolution_fit()
        accounts = SettingsManager.instance().accounts

        account_cards: list[QWidget] = []
        for account in accounts:
            card = AccountCard(
                account,
                self._characters_for(account.get("username", "")),
                accounts,
                mode=CardMode.MANAGE,
            )
            card.edit_account_requested.connect(self._on_edit)
            card.delete_account_requested.connect(self._on_delete)
            account_cards.append(card)
        self._accounts_col.set_cards(account_cards)

        profile_cards: list[QWidget] = []
        for profile in SettingsManager.instance().launch_profiles:
            card = ProfileCard(profile, accounts, mode=CardMode.MANAGE)
            card.edit_profile_requested.connect(self._on_profile_edit)
            card.duplicate_profile_requested.connect(self._on_profile_duplicate)
            card.delete_profile_requested.connect(self._on_profile_delete)
            profile_cards.append(card)
        self._profiles_col.set_cards(profile_cards)

        self._refresh_fit()

    def _refresh_fit(self) -> None:
        """Note, on each column, what would not fit — see ``refresh_column_fit``.

        Handed the panel's size rather than this widget's: the dialog is not the game
        area, and measuring the tab would answer a question about the settings window.
        """
        refresh_column_fit(self._accounts_col, self._profiles_col, self, self._live_game_area)

    def _characters_for(self, username: str) -> list[dict]:
        """The known characters for *username*.

        Read straight off the per-account store, unlike the launch screen's injected
        roster source: that widget is built by the main window, which holds the
        ClientManager the rosters are cached on, and this dialog is not. The cache is
        a cache of exactly this file, so the two columns say the same thing.
        """
        return character_roster.load(username) if username else []

    def _on_accounts_reordered(self, order: list) -> None:
        if reorder_accounts(list(order)):
            # Both columns: an account's position moves the pills on every profile card
            # that carries it into a different color slot.
            self._refresh()

    def _on_profiles_reordered(self, order: list) -> None:
        if reorder_launch_profiles(list(order)):
            self._refresh()

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    def _on_add(self) -> None:
        if add_account(self):
            self._refresh()

    def _on_edit(self, username: str) -> None:
        if username and edit_account(username, self):
            self._refresh()

    def _on_delete(self, username: str) -> None:
        if username and delete_account(username, self):
            self._refresh()

    # ------------------------------------------------------------------
    # Launch Profiles
    # ------------------------------------------------------------------

    def _on_profile_add(self) -> None:
        if add_launch_profile(self, self._live_game_area):
            self._refresh()

    def _on_profile_edit(self, name: str) -> None:
        if name and edit_launch_profile(name, self, self._live_game_area):
            self._refresh()

    def _on_profile_duplicate(self, name: str) -> None:
        if name and duplicate_launch_profile(name):
            self._refresh()

    def _on_profile_delete(self, name: str) -> None:
        if name and delete_launch_profile(name, self):
            self._refresh()

    def apply(self) -> None:
        """Write the two launch options. Everything else here saved itself already.

        Accounts, launch profiles and the resolution all persist on the action that
        changed them -- a card is edited through its own dialog, and the launch screen
        behind this one has no OK button to wait for. The two check boxes are ordinary
        form rows, so they wait for OK like every other row in the dialog does.
        """
        sm = SettingsManager.instance()
        sm.auto_select_character = self._auto_select_cb.isChecked()
        sm.disable_quick_friend_teleport = self._quick_tp_cb.isChecked()
