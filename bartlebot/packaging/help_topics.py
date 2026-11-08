"""Which page of the site each surface of the app opens for help (epic #25, Stage 5).

F1, a dialog's ``?`` and the Help menu all open a page of the site at
:data:`SITE_URL`, and this module says which. A surface names itself with a
``help_topic``, a key of :data:`HELP_TOPICS`, and :func:`help_url` turns the key into
the page's address. ``gui/context_help.py`` is the half that needs Qt: it finds the
topic for the widget in front of the user and opens the page.

A topic's value is the page's path under ``site/pages/``, as a page links another,
with the heading's id where one helps. ``tests/packaging/test_help_topics.py`` holds
every value to a page that exists and an explicit id on it, so a page renamed or a
heading re-worded fails the suite rather than sending the user to a missing anchor.
``site/CLAUDE.md`` says a heading this map links keeps its id.

No Qt.
"""

from __future__ import annotations

from bartlebot.updates.feed import REPOSITORY

#: The site, the one place the app names it. ``site/zensical.toml``'s ``site_url`` is held to it.
SITE_URL = "https://bartleworks.github.io/BartleBot/"

#: Where a page with no better topic lands: the Guides section's index.
GUIDE_HOME = "guides/index.md"

#: Help › Troubleshooting.
TROUBLESHOOTING = "troubleshooting.md"

#: Help › Report a Problem: the repository's new-issue page, which Troubleshooting's
#: *Opening an issue* links too.
REPORT_PROBLEM_URL = f"{REPOSITORY}/issues/new"

#: A surface's help: its page under ``site/pages/``, and the heading's id where one helps.
HELP_TOPICS: dict[str, str] = {
    # The main window, by the canvas's mode.
    "run_mode": "guides/running/running-a-bot.md",
    "edit_mode": "guides/making/the-canvas.md#edit-mode",
    # The node browser with no node shown: a subgraph, or nothing selected. A node shown
    # opens its reference entry instead (node_help_url).
    "node_browser": "guides/making/the-canvas.md#add-a-node",
    # The combat guide (#317) is still to come; until it is, the node that fights.
    "combat_strategies": "reference/combat-and-healing.md#handle-combat",
    "templates": "guides/making/new-bot-and-templates.md#your-own-templates",
    "new_bot": "guides/making/new-bot-and-templates.md#new-bot",
    "import": "guides/making/sharing.md#import",
    "export": "guides/making/sharing.md#export",
    # Each Settings tab, by its name in the dialog.
    "settings_general": "guides/running/options-and-settings.md#which-settings-tab-holds-what",
    "settings_bot": "guides/running/options-and-settings.md#set-a-value-for-every-bot",
    "settings_combat": "guides/running/options-and-settings.md#which-settings-tab-holds-what",
    "settings_canvas": "guides/running/options-and-settings.md#which-settings-tab-holds-what",
    "settings_authoring": "guides/running/options-and-settings.md#which-settings-tab-holds-what",
    "settings_theme": "guides/running/options-and-settings.md#which-settings-tab-holds-what",
    "settings_game": "guides/running/accounts-clients-and-realms.md#add-your-accounts",
    # The Options tab: values in Run mode, declarations in Edit mode.
    "options": "guides/running/options-and-settings.md#set-your-own-value-for-one-bot",
    "options_edit": "guides/making/variables-and-conditions.md#branch-on-an-option",
    "variables": "guides/making/variables-and-conditions.md#declare-a-variable",
    "setup": "getting-started.md#answer-setups-questions",
    "safety_check": "guides/running/safety.md#read-the-safety-check-before-a-run",
    "entity_browser": "guides/making/the-canvas.md#entity-browser",
    "ui_tree_browser": "guides/making/the-canvas.md#ui-tree-browser",
}


def page_url(target: str) -> str:
    """The address of *target*, a page under ``site/pages/`` with an optional ``#id``.

    The site serves each page as a folder (``guides/index.md`` is ``guides/``,
    ``safety.md`` is ``safety/``), which is what a page's own links are turned into.
    """
    path, _, anchor = target.partition("#")
    stem = path.removesuffix(".md")
    if stem == "index" or stem.endswith("/index"):
        stem = stem.removesuffix("index")
    else:
        stem += "/"
    return SITE_URL + stem + (f"#{anchor}" if anchor else "")


def help_url(topic: str | None) -> str:
    """The page for *topic*, or the guide's home for a topic that isn't in :data:`HELP_TOPICS`."""
    return page_url(HELP_TOPICS.get(topic or "", GUIDE_HOME))


def node_target(node_type: str) -> str:
    """A node's reference entry, as a page under ``site/pages/`` and its id."""
    from bartlebot.nodes.reference import category_page, node_anchor  # noqa: PLC0415 - imports every node
    from bartlebot.nodes.registry import NODE_TYPE_TO_CATEGORY  # noqa: PLC0415

    return f"reference/{category_page(NODE_TYPE_TO_CATEGORY[node_type])}#{node_anchor(node_type)}"


def node_help_url(node_type: str) -> str:
    """The node browser's help for the node it shows: that node's reference entry."""
    return page_url(node_target(node_type))
