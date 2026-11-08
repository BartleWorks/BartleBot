"""The site's Reference pages, rendered from the specs the node browser draws (epic #25, Stage 2).

One page per node category, in ``NODE_CATEGORIES``' order, then Bot options and
Subgraphs, and the section's index. Every word on them comes from the code: a node's
``NodeSpec``, each ``BotOptionSpec`` a node declares, and the Official subgraph
definitions. A gap on a page is fixed where its text comes from, so the app gains it
too, and ``tools/build_site_reference.py`` is rerun. The pages are committed under
``site/pages/reference/``, because importing ``NODE_SPECS`` imports wizwalker, which
runs only on Windows, and the site is built on Ubuntu (``site/CLAUDE.md``, *Generated
pages*). ``tests/nodes/test_reference.py`` fails while the committed pages differ from a
fresh render.

Anchors are stable ids, never a heading's words: a node's is its type with hyphens
(``collect_wisps`` is ``#collect-wisps``), so renaming its label breaks no link. An
option's and a subgraph's are their names, lowercased and hyphenated.

No Qt: the Settings row titles an option names are written here, and the test holds
them to the constants the Settings dialog draws.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from bartlebot.bot.schema import SubgraphDefinition
from bartlebot.content.subgraphs.subgraph_store import official_subgraphs
from bartlebot.content.subgraphs.subgraphs import entry_nodes, exit_nodes, is_port_node, node_panels, port_name
from bartlebot.nodes.help_text import retry_limit_note, seconds
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField
from bartlebot.nodes.registry import NODE_CATEGORIES, NODE_SPECS, NODE_TYPE_TO_CATEGORY, node_display_name
from bartlebot.options.bot_options import APP_SETTING_KEYS, SOURCE_LEVELS, is_user_owned
from bartlebot.options.option_groups import GROUP_ORDER, GROUP_TITLES
from bartlebot.settings_manager import default_for

#: The command that writes the pages, named in each page's front matter and in the test's failure.
COMMAND = "uv run python tools/build_site_reference.py"

#: The section's folder, under the site's pages.
REFERENCE_DIR = Path("site") / "pages" / "reference"

INDEX_PAGE = "index.md"
OPTIONS_PAGE = "options.md"
SUBGRAPHS_PAGE = "subgraphs.md"

#: The Settings row that sets each option's app setting, on Settings › Bot, or None for an
#: app setting no row sets. One entry per ``APP_SETTING_KEYS`` option, and the titles are
#: the Settings dialog's own (``test_every_app_setting_names_the_row_the_settings_dialog_draws``).
SETTING_ROWS: dict[str, str | None] = {
    "Safety Delay": "Default Safety Delay",
    # The setting exists, but neither Settings nor setup has a row for it yet.
    "Level Scaling Players": None,
    "Refill Potions": "Refill potions",
    "Quicksell": "Quicksell",
    "Sell Pets": "Sell pets",
    "Sell Socket Wrenches": "Sell Socket Wrenches",
    "Sell Skeleton Keys": "Sell Skeleton Keys",
    "Friend Name": "Friend Name",
    "Favorite Realm": "Favorite Realm",
}

_FRONT_MATTER = (
    "---\n"
    "# Written by tools/build_site_reference.py, so an edit here is lost at the next render.\n"
    "# Fix the text where it comes from (site/CLAUDE.md, Generated pages), then run\n"
    f"# {COMMAND}\n"
    "generated: true\n"
    "---\n"
)

#: Characters a spec's text may hold that markdown would read as markup. ``<`` and ``&``
#: become entities instead, since a backslash does not escape them.
_MARKUP = re.compile(r"([\\`*_{}\[\]|#])")
#: What starts a list, a heading or a quote when it opens a line.
_LINE_START = re.compile(r"^(\s*)([-+]|\d+\.)(?=\s)", re.M)


def escape(text: str) -> str:
    """*text* as markdown that renders as itself: ``*Bandit*`` stays ``*Bandit*``, not italic."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _MARKUP.sub(r"\\\1", text)
    return _LINE_START.sub(lambda m: m[1] + m[2].replace(".", "\\.").replace("-", "\\-").replace("+", "\\+"), text)


def _paragraphs(text: str) -> list[str]:
    return [escape(p.strip()) for p in text.split("\n\n") if p.strip()]


def _cell(text: str) -> str:
    """*text* in one table cell, its paragraphs kept apart."""
    return "<br><br>".join(_paragraphs(text))


def slug(name: str) -> str:
    """An anchor from a name: lowercased, punctuation dropped, words joined by hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def node_anchor(node_type: str) -> str:
    """A node's anchor: its type with hyphens, so a renamed label breaks no link."""
    return node_type.replace("_", "-")


def category_page(category: str) -> str:
    """The page a node category is on: ``Combat and Healing`` is ``combat-and-healing.md``."""
    return f"{slug(category)}.md"


def node_link(node_type: str, *, from_page: str | None = None) -> str:
    """A link to a node's entry, by its display name, relative to the reference page *from_page*."""
    page = category_page(NODE_TYPE_TO_CATEGORY[node_type])
    target = f"#{node_anchor(node_type)}" if page == from_page else f"{page}#{node_anchor(node_type)}"
    return f"[{escape(node_display_name(node_type))}]({target})"


def option_link(name: str, *, from_page: str | None = None) -> str:
    target = f"#{slug(name)}" if from_page == OPTIONS_PAGE else f"{OPTIONS_PAGE}#{slug(name)}"
    return f"[{escape(name)}]({target})"


def _page(title: str, opener: str, body: Iterable[str]) -> str:
    blocks = [f"# {title}", opener, *body]
    return _FRONT_MATTER + "\n" + "\n\n".join(b.rstrip("\n") for b in blocks) + "\n"


# ---------------------------------------------------------------------------
# Values, in the words a user reads
# ---------------------------------------------------------------------------


def _player_list(players: Sequence[int]) -> str:
    return ", ".join(f"Player {n}" for n in players) if players else "none"


def param_default(field: ParamField) -> str:
    """The Default column: the param's hint, else what it starts as, in the hint's form (N7)."""
    if field.default_hint:
        return escape(field.default_hint)
    if field.required:
        return "required"
    value = field.default
    if field.type == "bool":
        return "on" if value else "off"
    if field.type == "ratio" and isinstance(value, int | float):
        return f"{round(value * 100)}%"
    if field.type == "player_slot":
        return f"Player {value}" if value else "none"
    if isinstance(value, int | float) and not isinstance(value, bool):
        return seconds(value) if field.label.endswith("(s)") else f"{value:g}"
    if field.type == "player_list" and isinstance(value, list):
        return _player_list(value)
    return escape(str(value)) if value not in (None, "", [], {}) else "none"


def _params_table(fields: Sequence[ParamField], heading: str) -> str:
    rows = [f"| {heading} | What it does | Default |", "|---|---|---|"]
    rows += [f"| **{escape(f.label)}** | {_cell(f.help)} | {param_default(f)} |" for f in fields]
    return "\n".join(rows)


def option_value(spec: BotOptionSpec) -> object:
    """What an option is when nothing sets it: the app setting's own default, for an option with one."""
    if spec.default is None and spec.name in APP_SETTING_KEYS:
        return default_for("general", APP_SETTING_KEYS[spec.name])
    return spec.default


def option_default(spec: BotOptionSpec) -> str:
    value = option_value(spec)
    if spec.type == "bool":
        return "On" if value else "Off"
    if isinstance(value, int | float) and not isinstance(value, bool):
        return seconds(value) if (spec.suffix or "").strip() == "s" else f"{value:g}"
    if spec.type == "player_list" and isinstance(value, list):
        return _player_list(value)
    return escape(str(value)) if value not in (None, "", [], {}) else "none"


# ---------------------------------------------------------------------------
# Node pages
# ---------------------------------------------------------------------------


def _param_label(spec: NodeSpec, key: str) -> str:
    return next((f.label for f in spec.params if f.key == key), key)


def _option_condition(spec: NodeSpec, option: BotOptionSpec, *, form: str = ", while {param} is {state}") -> str:
    """When a node adds *option*: always (""), or while one of its own params is on or off, in *form*."""
    if option.required_when:
        return form.format(param=f"**{escape(_param_label(spec, option.required_when))}**", state="on")
    if option.required_unless:
        return form.format(param=f"**{escape(_param_label(spec, option.required_unless))}**", state="off")
    return ""


def render_node(node_type: str, spec: NodeSpec, *, page: str) -> str:
    """One node's entry: what the node browser's page shows for it, in the same order."""
    parts = [f"## {escape(node_display_name(node_type))} {{ #{node_anchor(node_type)} }}"]
    parts += _paragraphs(spec.description)
    if spec.max_consecutive_retries is not None:
        parts.append(escape(retry_limit_note(spec.max_consecutive_retries)))
    if spec.params:
        parts += ["**Parameters**", _params_table(spec.params, "Parameter")]
    else:
        parts.append("This node has no parameters.")
    if spec.returns:
        parts.append("**Returns**")
        parts.append("\n".join(f"- **{escape(r.name)}**: {escape(r.description)}" for r in spec.returns))
    if spec.required_bot_options:
        parts.append("**Bot options** it adds to the bot's **Options** tab:")
        parts.append(
            "\n".join(f"- {option_link(o.name)}{_option_condition(spec, o)}" for o in spec.required_bot_options)
        )
    if spec.safety_warning:
        warning = "\n\n".join(f"    {p}" for p in _paragraphs(spec.safety_warning))
        parts.append(f'!!! warning "Shown before a run"\n\n{warning}')
    if spec.usage_notes:
        parts += ["**Usage notes**", *_paragraphs(spec.usage_notes)]
    if spec.see_also:
        parts.append("**See also:** " + ", ".join(node_link(t, from_page=page) for t in spec.see_also))
    return "\n\n".join(parts)


def render_category_page(name: str, node_types: Sequence[str]) -> str:
    """A node category's page: one entry per node, in the node browser's order."""
    page = category_page(name)
    return _page(
        escape(name),
        f"The nodes in the node browser's **{escape(name)}** group, with the help it shows for each one.",
        (render_node(t, NODE_SPECS[t], page=page) for t in node_types),
    )


# ---------------------------------------------------------------------------
# The options page
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeOption:
    """One option a node declares, with every node that declares it, in node browser order."""

    spec: BotOptionSpec
    declared_by: tuple[tuple[str, BotOptionSpec], ...]


def node_options() -> list[NodeOption]:
    """Every distinct option a node declares, by its card, its card's switch first, then by name.

    One option's declarations agree on its text and card
    (``test_every_declaration_of_one_option_agrees_on_its_card``), so the first stands for all.
    """
    found: dict[str, list[tuple[str, BotOptionSpec]]] = {}
    for _category, node_types in NODE_CATEGORIES:
        for t in node_types:
            for o in NODE_SPECS[t].required_bot_options:
                found.setdefault(o.name, []).append((t, o))
    options = [NodeOption(decls[0][1], tuple(decls)) for decls in found.values()]
    return sorted(
        options, key=lambda o: (GROUP_ORDER.index(o.spec.group), not o.spec.heads_group, o.spec.name.casefold())
    )


def _render_option(option: NodeOption) -> str:
    spec = option.spec
    parts = [f"### {escape(spec.name)} {{ #{slug(spec.name)} }}"]
    parts += _paragraphs(spec.description or "")
    facts = [f"- **Default:** {option_default(spec)}"]
    row = SETTING_ROWS.get(spec.name)
    if row:
        facts.append(f"- **App setting:** **Settings › Bot › {escape(row)}**")
    if is_user_owned(spec.name):
        facts.append("- **Bot default:** none. A bot's author can't set this option.")
    if spec.depends_on:
        facts.append(f"- **Used while** {option_link(spec.depends_on, from_page=OPTIONS_PAGE)} is on")
    added_by = ", ".join(
        node_link(t, from_page=OPTIONS_PAGE) + _option_condition(NODE_SPECS[t], o, form=" with {param} {state}")
        for t, o in option.declared_by
    )
    facts.append(f"- **Added by:** {added_by}")
    parts.append("\n".join(facts))
    return "\n\n".join(parts)


def _levels() -> str:
    override, bot_file, app_setting, node_default = SOURCE_LEVELS
    return "\n".join(
        [
            "## Where an option's value comes from { #where-a-value-comes-from }",
            "",
            "A run takes each option's value from the first of these that has one:",
            "",
            f"1. **{override}**: what you set for this bot in its **Options** tab.",
            f"2. **{bot_file}**: what the bot's author set for it.",
            f"3. **{app_setting}**: what you set in **Settings › Bot**, for an option with a row there.",
            f"4. **{node_default}**: the default under each option below.",
            "",
            "Your values are kept in your settings, so the bot file stays as its author wrote it.",
        ]
    )


def render_options_page() -> str:
    """Every option a node declares, by the card the **Options** tab draws it in."""
    body = [_levels()]
    by_group: dict[str, list[NodeOption]] = {}
    for option in node_options():
        by_group.setdefault(option.spec.group, []).append(option)
    for group in GROUP_ORDER:
        if group not in by_group:
            continue
        title = GROUP_TITLES[group]
        body.append(f"## {escape(title)} {{ #{slug(title)}-card }}")
        body += [_render_option(o) for o in by_group[group]]
    return _page(
        "Bot options",
        "Every option a node adds to a bot, by the card it's in on the bot's **Options** tab. "
        "A bot's author can add options of their own, which the bot's description explains.",
        body,
    )


# ---------------------------------------------------------------------------
# The subgraphs page
# ---------------------------------------------------------------------------


def _render_subgraph(defn: SubgraphDefinition) -> str:
    parts = [f"## {escape(defn.name)} {{ #{slug(defn.name)} }}"]
    parts += _paragraphs(defn.description or "")
    if defn.tags:
        parts.append("**Categories:** " + ", ".join(escape(t) for t in defn.tags))
    fields = [
        ParamField(key=i.name, type=i.type, label=i.label or i.name, help=i.help or "", default=i.default)
        for i in defn.inputs
    ]
    by_id = {s.id: s for s in defn.states}
    panels = [p for p in node_panels(defn) if p.member in by_id]
    if fields or panels:
        parts.append("**Inputs**")
    if fields:
        parts.append(_params_table(fields, "Input"))
    if panels:
        parts.append(
            "\n".join(
                f"- **{escape(by_id[p.member].label or p.label or p.member)}**: every parameter of "
                f"{node_link(by_id[p.member].node_type)}"
                for p in panels
            )
        )
    if not fields and not panels:
        parts.append("This subgraph has no inputs.")
    entries = ", ".join(escape(port_name(s)) for s in entry_nodes(defn)) or "none"
    exits = ", ".join(escape(port_name(s)) for s in exit_nodes(defn)) or "none"
    parts += ["**Ports**", f"- **Entries:** {entries}\n- **Exits:** {exits}"]
    runs = dict.fromkeys(s.node_type for s in defn.states if not is_port_node(s) and s.node_type in NODE_SPECS)
    if runs:
        parts.append("**Nodes it runs:** " + ", ".join(node_link(t) for t in runs))
    return "\n\n".join(parts)


def render_subgraphs_page(root: str | Path | None = None) -> str:
    """Each Official subgraph, read from the shipped root alone (*root*, the install by default)."""
    definitions = official_subgraphs(root)
    body = [_render_subgraph(d) for d in definitions] or ["No subgraph comes with BartleBot yet."]
    return _page(
        "Subgraphs",
        "The Official subgraphs, which come with BartleBot and are listed on the node browser's **Subgraphs** tab.",
        body,
    )


# ---------------------------------------------------------------------------
# The section
# ---------------------------------------------------------------------------


def render_index_page() -> str:
    lines = [
        f"- [{escape(name)}]({category_page(name)}): " + ", ".join(escape(node_display_name(t)) for t in types)
        for name, types in NODE_CATEGORIES
    ]
    lines.append(f"- [Bot options]({OPTIONS_PAGE}): every option a node adds to a bot, and where its value comes from")
    lines.append(f"- [Subgraphs]({SUBGRAPHS_PAGE}): the Official subgraphs, their inputs and their ports")
    return _page(
        "Reference",
        "Every node, bot option and Official subgraph, in the words the app shows for each one. "
        "Each group in the node browser has a page, and so do bot options and subgraphs.",
        ["\n".join(lines)],
    )


def page_names() -> list[str]:
    """The section's pages in nav order: the index, each category, Bot options, Subgraphs."""
    return [INDEX_PAGE, *(category_page(name) for name, _types in NODE_CATEGORIES), OPTIONS_PAGE, SUBGRAPHS_PAGE]


def render_pages(subgraph_root: str | Path | None = None) -> dict[str, str]:
    """``{file name: text}`` for every page of the section, in nav order."""
    pages = {INDEX_PAGE: render_index_page()}
    pages.update({category_page(name): render_category_page(name, types) for name, types in NODE_CATEGORIES})
    pages[OPTIONS_PAGE] = render_options_page()
    pages[SUBGRAPHS_PAGE] = render_subgraphs_page(subgraph_root)
    return pages


def is_generated(path: Path) -> bool:
    """Whether the page at *path* says in its front matter that a script wrote it."""
    match = re.match(r"---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    return bool(match) and re.search(r"^generated: true$", match[1], re.M) is not None


def stale_pages(folder: Path, pages: dict[str, str]) -> list[str]:
    """The pages under *folder* that differ from *pages*, are missing, or are generated and no longer produced."""
    stale = [name for name, text in pages.items() if _read(folder / name) != text]
    stale += sorted(p.name for p in folder.glob("*.md") if p.name not in pages and is_generated(p))
    return stale


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def write_pages(folder: Path, pages: dict[str, str]) -> tuple[list[str], list[str]]:
    """Write *pages* into *folder* and delete each generated page no longer produced.

    Returns ``(written, deleted)``, the pages that changed. A page in the folder that
    is not marked generated is left alone.
    """
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in pages.items():
        if _read(folder / name) != text:
            # newline="\n" so a Windows checkout writes what the test compares against.
            (folder / name).write_text(text, encoding="utf-8", newline="\n")
            written.append(name)
    deleted = sorted(p.name for p in folder.glob("*.md") if p.name not in pages and is_generated(p))
    for name in deleted:
        (folder / name).unlink()
    return written, deleted
