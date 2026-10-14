"""The combat DSL's vocabulary, and what kind of word belongs at a given point in it.

Feeds the DSL editor's highlighting and completion (``gui/widgets/inputs/dsl_edit.py``).
Pure — no Qt, no wizwalker — so all of it is testable without a ``QApplication``.

This module *scans*; it does not parse. The grammar is wizsprinter's
(``combat_config_parser.get_sprinty_grammar``) and ``combat_runner.dsl_error`` is
still the only judge of whether a DSL is valid. A parser cannot do this job: it
has nothing to say about half a line, which is exactly what an editor holds while
someone types, and Lark's "what could come next" query only exists for its LALR
mode, which this grammar does not use.

What the scanner has to get right is that **one word means different things in
different places**. ``aoe`` is a target after ``@`` and a spell type inside
``any<…>``; ``self`` is a target, a condition subject, and half of ``heal self``;
and a word that is none of these is a spell name or a combatant's name. So it
keeps a stack of the brackets it is inside, and classifies each word against the
innermost.

The word lists are copied here rather than read from wizsprinter at run time,
because importing the grammar module imports wizwalker. ``tests/combat/test_dsl_syntax.py``
checks each list against the grammar and the combat code, so a keyword added
upstream fails a test rather than going quietly uncolored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum

#: Spell types allowed inside ``any<…>``. ``heal`` may be followed by ``self`` or
#: ``other`` — two words, since ``heal_self`` does not parse.
SPELL_TYPES: tuple[str, ...] = (
    "damage",
    "aoe",
    "heal",
    "blade",
    "charm",
    "ward",
    "trap",
    "enchant",
    "aura",
    "harmful_aura",
    "global",
    "polymorph",
    "shadow",
    "shadow_creature",
    "pierce",
    "prism",
    "dispel",
    "inc_damage",
    "out_damage",
    "inc_heal",
    "out_heal",
    "mod_damage",
    "mod_heal",
    "mod_pierce",
    "req_met",
    "stacks",
    "stronger",
)
HEAL_QUALIFIERS: tuple[str, ...] = ("self", "other")
#: What ``!`` can negate: ``any<trap & !aoe>``. ``req_met``, ``stacks``, ``stronger`` and
#: the hanging verbs are checked after a card is picked, so there is nothing for a negation
#: of one to test. Any other word after ``!`` is a card name to exclude: ``any<trap & !feint>``.
NEGATABLE_TYPES: tuple[str, ...] = tuple(t for t in SPELL_TYPES if t not in ("req_met", "stacks", "stronger"))
#: Spell types that take arguments: ``gambit(harmful_charms, 2)``.
HANGING_VERBS: tuple[str, ...] = ("gambit", "clear", "echo", "swap")

#: ``combat_api.HANGING_CATEGORIES`` — canonical name, then its aliases.
_HANGING_CATEGORIES: tuple[tuple[str, ...], ...] = (
    ("charms", "charm"),
    ("wards", "ward"),
    ("over_time", "ot"),
    ("auras", "aura"),
)
#: What a hanging verb takes, and what a condition can count: every category name
#: and alias, bare or with a ``beneficial_`` / ``harmful_`` side.
HANGING_KINDS: tuple[str, ...] = tuple(
    f"{side}{name}" for names in _HANGING_CATEGORIES for name in names for side in ("", "beneficial_", "harmful_")
)

TARGETS: tuple[str, ...] = ("self", "boss", "enemy", "enemies", "ally", "allies", "aoe")
#: Target forms that take a list: ``select(enemy, boss)``, ``spell(Tempest)``.
TARGET_FORMS: tuple[str, ...] = ("select", "spell")
#: ``enemy(1)``, ``ally(last)``.
INDEXED_TARGETS: tuple[str, ...] = ("enemy", "ally")

#: Moves that are not a spell. ``any`` starts a spell type, so it is offered as ``any<``.
#: ``discard`` throws cards away rather than casting, and names them as its target:
#: ``discard @ spell(Tempest)``. The grammar's own ``move_discard`` rule is unreachable —
#: the word parses as a spell name, which ``SprintyCombat.try_get_spell`` special-cases.
MOVE_KEYWORDS: tuple[str, ...] = ("pass", "willcast", "draw", "discard")

#: ``deck`` is the wizard's own deck, and reads only :data:`DECK_ATTRIBUTES`.
CONDITION_SUBJECTS: tuple[str, ...] = ("self", "boss", "enemy", "ally", "deck")
#: ``any(enemies).health < 50%``.
AGGREGATES: tuple[str, ...] = ("any", "all", "avg")
AGGREGATE_GROUPS: tuple[str, ...] = ("enemies", "allies")
#: What ``self.<attribute>`` can read: a ``CombatMember`` method returning a number.
#: A percentage divides by ``max_<attribute>``, so only ``health`` and ``mana`` take one.
MEMBER_ATTRIBUTES: tuple[str, ...] = (
    "health",
    "max_health",
    "mana",
    "max_mana",
    "normal_pips",
    "power_pips",
    "shadow_pips",
    "level",
)
CONDITION_ATTRIBUTES: tuple[str, ...] = MEMBER_ATTRIBUTES + HANGING_KINDS
#: What ``deck.<attribute>`` can read: cards left in the main pile.
DECK_ATTRIBUTES: tuple[str, ...] = ("remaining",)
_AFTER_DECK = re.compile(r"\bdeck\s*\.\s*\Z", re.IGNORECASE)

DSL_HELP = (
    "{round} move @ target | move @ target | pass — rounds optional, '|' is priority order.\n"
    'Moves: "Named Spell" or any<damage|heal|blade|trap|charm|ward|aura|...>[enchant]\n'
    "Combine types with &, and rule one out with !: any<trap & !aoe>, or a card by name: any<trap & !feint>\n"
    'Targets: self, enemy, boss, ally, aoe, or a name — Nightshade, or "Lord Nightshade"\n'
    "Pick one out with ally(0) or enemy(1). ally(last) is the last player on the team, you included,\n"
    "so every client in the group can share the line.\n"
    "Ctrl+Space suggests what can go at the cursor."
)


class Context(IntEnum):
    """What the scanner is inside. Values fit in four bits — see :func:`encode_stack`."""

    MOVE = 1  # a move: a spell name, any<…>, or a move keyword
    TARGET = 2  # after @
    SPELL_TYPE = 3  # any<…>
    HANGING = 4  # gambit(…) and its siblings
    CONDITION = 5  # ?(…)
    AGGREGATE = 6  # any(…) inside a condition
    INDEX = 7  # enemy(…), ally(…), draw(…)
    SELECT = 8  # select(…)
    SPELL_LIST = 9  # spell(…)
    ENCHANT = 10  # […]
    ROUND = 11  # {…}
    #: After ``discard @``. Every target still parses here, but only ``spell(…)`` works:
    #: anything else reaches the card lookup as a combatant and raises mid-fight.
    DISCARD_TARGET = 12


#: The contexts right after ``@``. They color alike and differ only in what is offered.
_AFTER_AT = (Context.TARGET, Context.DISCARD_TARGET)


class Kind(StrEnum):
    """How a span is drawn. Each names a ``syntax_*`` theme token."""

    KEYWORD = "syntax_keyword"
    TYPE = "syntax_type"
    TARGET = "syntax_target"
    NUMBER = "syntax_number"
    STRING = "syntax_string"
    PUNCT = "syntax_punct"


@dataclass(frozen=True)
class Span:
    start: int
    length: int
    kind: Kind


@dataclass(frozen=True)
class LineScan:
    spans: tuple[Span, ...]
    #: The brackets still open at the end of the line, outermost first.
    stack: tuple[Context, ...]
    #: The last token that was not whitespace, or None for a blank line.
    last: str | None
    #: Whether the line ends inside a string with no closing quote.
    open_string: bool


_BASE: tuple[Context, ...] = (Context.MOVE,)

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
    | (?P<string>"(?:[^"\\]|\\.)*"?)
    | (?P<number>-?\d+(?:\.\d+)?%?)
    | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<punct>\?\(|&&|<=|>=|!=|==|[@|&<>()\[\]{},.%!])
    | (?P<other>.)
    """,
    re.VERBOSE,
)

#: Where a bare number is a number. Anywhere else it is part of a name — "Storm Lord 2".
_NUMERIC_CONTEXTS = frozenset({Context.ROUND, Context.INDEX, Context.HANGING, Context.CONDITION})

#: The tokens a filter inside ``any<…>`` follows. A word after anything else continues a name.
#: None is the start of a line, which inside ``any<…>`` follows one of these on the last.
_FILTER_STARTS = frozenset({"<", "&", "!", None})

#: Contexts whose pops are driven by a specific closing bracket.
_CLOSERS = {")": None, ">": Context.SPELL_TYPE, "]": Context.ENCHANT, "}": Context.ROUND}


def scan_line(line: str, stack: tuple[Context, ...] = _BASE) -> LineScan:
    """Classify every token on *line*, starting inside *stack*."""
    contexts = list(stack) or list(_BASE)
    spans: list[Span] = []
    last: str | None = None
    open_string = False
    for match in _TOKEN.finditer(line):
        group, text, start = match.lastgroup, match.group(), match.start()
        if group == "ws":
            continue
        top = contexts[-1]
        kind: Kind | None = None
        if group == "string":
            kind = Kind.STRING
            open_string = len(text) == 1 or not text.endswith('"') or text.endswith('\\"')
        elif group == "number":
            kind = Kind.NUMBER if top in _NUMERIC_CONTEXTS else None
        elif group == "word":
            kind = _classify_word(text, top, last)
        elif group == "punct":
            kind = Kind.PUNCT
            _apply_punct(text, contexts, last)
        if kind is not None:
            spans.append(Span(start, len(text), kind))
        last = text
    return LineScan(tuple(spans), tuple(contexts), last, open_string)


def _classify_word(word: str, top: Context, last: str | None) -> Kind | None:
    lowered = word.lower()
    match top:
        case Context.MOVE:
            return Kind.KEYWORD if lowered in MOVE_KEYWORDS or lowered == "any" else None
        case Context.ENCHANT | Context.SPELL_LIST:
            return Kind.KEYWORD if lowered == "any" else None
        case Context.TARGET | Context.DISCARD_TARGET | Context.SELECT:
            if lowered in TARGETS:
                return Kind.TARGET
            return Kind.KEYWORD if top in _AFTER_AT and lowered in TARGET_FORMS else None
        case Context.SPELL_TYPE:
            if last == "heal" and lowered in HEAL_QUALIFIERS:
                return Kind.TYPE
            # After ``!`` a word that is not a type starts a card name, and every word
            # after that is part of it: the Trap in ``!Fire Trap`` is not the type.
            if last not in _FILTER_STARTS:
                return None
            if lowered in HANGING_VERBS:
                return Kind.KEYWORD
            return Kind.TYPE if lowered in SPELL_TYPES else None
        case Context.HANGING:
            return Kind.TYPE if lowered in HANGING_KINDS else None
        case Context.CONDITION:
            if last == ".":
                return Kind.TYPE
            if lowered in CONDITION_SUBJECTS:
                return Kind.TARGET
            return Kind.KEYWORD if lowered in AGGREGATES else None
        case Context.AGGREGATE:
            return Kind.TARGET if lowered in AGGREGATE_GROUPS else None
        case Context.INDEX:
            return Kind.KEYWORD if lowered == "last" else None
    return None


def _apply_punct(text: str, contexts: list[Context], last: str | None) -> None:
    """Open, close, or switch context for one punctuation token."""
    top = contexts[-1]
    word = last.lower() if last else None
    if text == "?(":
        contexts.append(Context.CONDITION)
    elif text == "(":
        contexts.append(_paren_context(top, word))
    elif text == "<" and word == "any":
        contexts.append(Context.SPELL_TYPE)
    elif text == "[":
        contexts.append(Context.ENCHANT)
    elif text == "{":
        contexts.append(Context.ROUND)
    elif text in _CLOSERS and len(contexts) > 1:
        wanted = _CLOSERS[text]
        if wanted is None or top is wanted:
            contexts.pop()
    elif text == "@" and len(contexts) == 1:
        contexts[0] = Context.DISCARD_TARGET if word == "discard" else Context.TARGET
    elif text == "&" and len(contexts) == 1:
        contexts[0] = Context.MOVE
    elif text == "|":
        contexts[:] = list(_BASE)


def _paren_context(top: Context, word: str | None) -> Context:
    if top is Context.MOVE and word == "draw":
        return Context.INDEX
    if top in (*_AFTER_AT, Context.SELECT, Context.CONDITION) and word in INDEXED_TARGETS:
        return Context.INDEX
    if top in _AFTER_AT and word == "select":
        return Context.SELECT
    if top in _AFTER_AT and word == "spell":
        return Context.SPELL_LIST
    if top is Context.SPELL_TYPE and word in HANGING_VERBS:
        return Context.HANGING
    if top is Context.CONDITION and word in AGGREGATES:
        return Context.AGGREGATE
    # A bracket the grammar gives no meaning here: stay in the same context, so its
    # closing bracket has something to pop.
    return top


# ---------------------------------------------------------------------------
# Across lines
# ---------------------------------------------------------------------------

_MAX_DEPTH = 7  # 4 bits each, inside QSyntaxHighlighter's signed 32-bit block state


def next_line_stack(stack: tuple[Context, ...]) -> tuple[Context, ...]:
    """The stack a new line starts in, given where the previous one ended.

    An open bracket carries over — the grammar allows a newline almost anywhere
    inside one. A line with nothing open is a finished line, and the next one
    starts a new move: carrying an ``@`` over would color every spell name on
    the following line as a target.
    """
    return stack if len(stack) > 1 else _BASE


def encode_stack(stack: tuple[Context, ...]) -> int:
    """Pack a stack into a non-negative int, for ``QSyntaxHighlighter.setCurrentBlockState``."""
    state = 0
    for depth, context in enumerate(stack[:_MAX_DEPTH]):
        state |= int(context) << (4 * depth)
    return state


def decode_stack(state: int) -> tuple[Context, ...]:
    """Unpack :func:`encode_stack`. Qt's "no state yet", ``-1``, is the base stack."""
    if state <= 0:
        return _BASE
    stack = []
    while state:
        stack.append(Context(state & 0xF))
        state >>= 4
    return tuple(stack)


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Completion:
    """What can go at the cursor."""

    #: The word already typed at the cursor, which a chosen candidate replaces.
    prefix: str
    #: Candidates starting with ``prefix``, case-insensitively, in the order offered.
    candidates: tuple[str, ...]
    #: Whether the context alone warrants suggesting, before anything is typed. True
    #: after ``@``, ``any<``, ``?(`` and the like, where only a keyword can follow; false
    #: at the start of a move, where the likeliest thing typed is a spell name.
    eager: bool


_NOTHING = Completion("", (), False)
# \Z, not $: $ also matches just before a trailing newline, which would read the last
# word of the previous line as the word being typed.
_PARTIAL_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def complete(before_cursor: str) -> Completion:
    """What can be typed at the end of *before_cursor* — the text up to the cursor."""
    match = _PARTIAL_WORD.search(before_cursor)
    prefix = match.group() if match else ""
    head = before_cursor[: len(before_cursor) - len(prefix)]
    # A word that runs straight on from a digit ("2nd") is not the start of a keyword.
    if prefix and head[-1:].isdigit():
        return _NOTHING

    stack: tuple[Context, ...] = _BASE
    last: str | None = None
    for line in head.split("\n"):
        carried = next_line_stack(stack)
        # Inside an open bracket the newline is just whitespace, so the token before it
        # still decides what comes next; a finished line starts the next move afresh.
        carried_last = last if len(carried) > 1 else None
        scan = scan_line(line, carried)
        stack, last = scan.stack, scan.last if scan.last is not None else carried_last
    if scan.open_string:
        return _NOTHING

    offered = _offered(stack, last.lower() if last else None)
    # The scanner keeps only the last token, so which subject the dot follows is read here.
    if stack[-1] is Context.CONDITION and last == "." and _AFTER_DECK.search(head):
        offered = DECK_ATTRIBUTES
    wanted = prefix.lower()
    candidates = tuple(c for c in offered if c.lower().startswith(wanted))
    eager = bool(offered) and stack[-1] not in (Context.MOVE, Context.ENCHANT, Context.SPELL_LIST)
    return Completion(prefix, candidates, eager)


def _offered(stack: tuple[Context, ...], last: str | None) -> tuple[str, ...]:
    top = stack[-1]
    match top:
        case Context.MOVE if last in (None, "|", "&", "}"):
            return ("any<", *MOVE_KEYWORDS)
        case Context.ENCHANT if last == "[":
            return ("any<",)
        case Context.SPELL_LIST if last in ("(", ","):
            return ("any<",)
        case Context.TARGET if last in ("@", ","):
            return (*TARGETS, "select(", "spell(")
        case Context.DISCARD_TARGET if last == "@":
            return ("spell(",)
        case Context.SELECT if last in ("(", ","):
            return TARGETS
        case Context.SPELL_TYPE if last in ("<", "&"):
            return (*SPELL_TYPES, *(f"{verb}(" for verb in HANGING_VERBS))
        case Context.SPELL_TYPE if last == "!":
            return NEGATABLE_TYPES
        case Context.SPELL_TYPE if last == "heal":
            return HEAL_QUALIFIERS
        case Context.HANGING if last == "(":
            return HANGING_KINDS
        case Context.CONDITION if last in ("?(", "&&"):
            return (*CONDITION_SUBJECTS, *(f"{agg}(" for agg in AGGREGATES))
        case Context.CONDITION if last == ".":
            return CONDITION_ATTRIBUTES
        case Context.AGGREGATE if last == "(":
            return AGGREGATE_GROUPS
        case Context.INDEX if last == "(":
            return ("last",)
    return ()
