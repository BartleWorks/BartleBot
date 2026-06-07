"""Pydantic models for Bot Combat Profiles, Duel Profiles, and Combat Behaviors."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class CombatBehavior(BaseModel):
    """A DSL specification applied to a subset of players within a duel."""

    players: list[int] = Field(default_factory=list)  # 1-based slot numbers, e.g. [2, 3, 4]
    dsl: str = ""


class DuelProfile(BaseModel):
    """Combat configuration for a single fight encounter.

    Contains one or more CombatBehaviors; each behavior specifies the DSL and
    which player slots execute it.  If use_bartlebot_default is True the
    behaviors list is ignored at runtime and the built-in default DSL is used.
    """

    name: str
    use_bartlebot_default: bool = True
    behaviors: list[CombatBehavior] = Field(default_factory=list)
    combat_order_override: list[int] | None = None  # None = inherit from BotCombatProfile

    def resolve_dsl_for_client(self, client_index: int) -> str | None:
        """Return the DSL string for the given 0-based client index, or None to use built-in default.

        Matches the first CombatBehavior whose players list contains the
        1-based slot number.  Falls back to the first behavior if no explicit
        assignment exists.  Returns None when use_bartlebot_default is True.
        """
        if self.use_bartlebot_default:
            return None
        slot = client_index + 1  # 0-based → 1-based
        for behavior in self.behaviors:
            if slot in behavior.players:
                return behavior.dsl
        # No explicit assignment — fall back to first behavior
        if self.behaviors:
            return self.behaviors[0].dsl
        return None


class DeckPreset(BaseModel):
    """Saved deck configuration in WizWalker preset format.

    Each key maps card names to copy counts.  Mirrors the dict returned by
    DeckBuilder.get_deck_preset(): 'normal' for main-deck spells, 'tc' for
    treasure cards, 'item' for item cards.
    """

    normal: dict[str, int] = Field(default_factory=dict)
    tc: dict[str, int] = Field(default_factory=dict)
    item: dict[str, int] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.normal or self.tc or self.item)

    def total_cards(self) -> int:
        return sum(self.normal.values()) + sum(self.tc.values()) + sum(self.item.values())


class PlayerDeck(BaseModel):
    """Deck configuration for a single player slot."""

    preset: DeckPreset | None = None


class BotCombatProfile(BaseModel):
    """Complete combat setup for one playthrough configuration of a bot.

    A bot may store multiple BotCombatProfiles (e.g. "Fire Team", "Storm Team").
    Each profile contains:
    - combat_order: default sequential entry order (e.g. [2, 1] sends P2 first)
    - player_decks: which deck each wizard slot loads before combat
    - duel_profiles: per-duel DSL configuration keyed by name; always
      contains a "default" entry used for handle_combat nodes that do not
      declare a duel_profile_name.
    """

    name: str
    combat_order: list[int] = Field(default_factory=list)  # profile-level default; [] = simultaneous
    player_decks: dict[int, PlayerDeck] = Field(default_factory=dict)
    duel_profiles: dict[str, DuelProfile] = Field(default_factory=lambda: {"default": DuelProfile(name="default")})

    @model_validator(mode="after")
    def ensure_default_duel_profile(self) -> BotCombatProfile:
        if "default" not in self.duel_profiles:
            self.duel_profiles["default"] = DuelProfile(name="default")
        return self

    def get_duel_profile(self, name: str) -> DuelProfile:
        """Return the named DuelProfile, falling back to 'default'."""
        return self.duel_profiles.get(name) or self.duel_profiles["default"]

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.stem + "_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.model_dump(exclude_none=True), f, indent=2)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str | Path) -> BotCombatProfile:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))


# ---------------------------------------------------------------------------
# Utility helpers for combat order UI fields
# ---------------------------------------------------------------------------


def parse_combat_order(text: str) -> list[int]:
    """Parse a comma-separated order string (e.g. '2, 1') → [2, 1]. Returns [] on empty/invalid."""
    if not text.strip():
        return []
    try:
        return [int(x.strip()) for x in text.split(",") if x.strip()]
    except ValueError:
        return []


def format_combat_order(order: list[int]) -> str:
    """Format [2, 1] → '2, 1'."""
    return ", ".join(str(x) for x in order)
