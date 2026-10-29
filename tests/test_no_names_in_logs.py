"""No log call names an account or a wizard (#241), read from BartleBot's own source.

Split from ``tests/test_logging_setup.py`` when ``logging_setup`` moved to bartleby-core in
Stage 5 of #167: the rest of that file's tests went with the module, and this one reads
BartleBot's package.
"""

import ast

import pytest

#: A logging call: a logger's level methods, ``machine.log`` and the machine's own.
_LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log", "_log_machine"})
#: What a log call must never be handed. An account's username is half of a login; a
#: wizard name identifies a player. Both go into session files, which get pasted into issues.
_UNLOGGABLE = frozenset({"username", "usernames", "friend_name", "wizard_name", "cached_name", "_client_order"})
#: Calls that turn one of those into something a log may hold, so a name inside them is fine.
_SANITIZERS = frozenset({"account_label", "_account_label", "_slot_order_for_log", "display_name", "mask", "len"})


def _called_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_log_call(call: ast.Call) -> bool:
    name = _called_name(call.func)
    if name not in _LOG_METHODS:
        return False
    if not isinstance(call.func, ast.Attribute):
        return True
    # ``logger.warning`` yes, ``QMessageBox.warning`` no.
    receiver = _called_name(call.func.value) or ""
    return "log" in receiver.lower() or "machine" in receiver.lower() or receiver == "self"


def _unloggable_names(expr: ast.AST) -> list[str]:
    found: list[str] = []
    stack = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call) and _called_name(node.func) in _SANITIZERS:
            continue
        if (
            isinstance(node, ast.Call)
            and _called_name(node.func) == "map"
            and _called_name(node.args[0]) in _SANITIZERS
        ):
            continue
        if isinstance(node, ast.Name) and node.id in _UNLOGGABLE:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _UNLOGGABLE:
            found.append(node.attr)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _log_leaks(source: str) -> list[tuple[int, str]]:
    """``(line, name)`` for every log call in *source* handed a username or a wizard name."""
    leaks = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _is_log_call(node):
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                leaks.extend((node.lineno, name) for name in _unloggable_names(arg))
    return leaks


def test_no_log_call_names_an_account_or_a_wizard():
    """CLAUDE.md, *Logging*: never a wizard name, never an account's username.

    Every line this replaced was written by someone who knew the wizard-name rule, so the
    rule alone does not hold. Name an account by ``account_label`` (its alias, or the
    username censored) or by its slot; say a wizard was found, not who.
    """
    from bartlebot.paths import project_root  # noqa: PLC0415

    root = project_root()
    leaks = [
        f"{path.relative_to(root).as_posix()}:{line} passes {name}"
        for path in (root / "bartlebot").rglob("*.py")
        for line, name in _log_leaks(path.read_text(encoding="utf-8"))
    ]
    assert leaks == []


@pytest.mark.parametrize(
    "source",
    [
        "logger.info('Reserved slot order: %s', self._client_order)",
        "logger.warning('account %s', username)",
        "logger.debug(f'friend {friend_name!r}')",
        "machine.log(f'Porting to {wizard_names.cached_name(client)}')",
        "self._log_machine('closed %s', t.username)",
        "logger.info('accounts: %s', ', '.join(usernames))",
    ],
)
def test_the_check_catches_a_name_in_a_log_call(source):
    assert _log_leaks(source)


@pytest.mark.parametrize(
    "source",
    [
        "logger.info('account %s', account_label(username))",
        "logger.info('%s', ', '.join(map(account_label, usernames)))",
        "QMessageBox.warning(parent, 'Duplicate', f'Account {username} already exists.')",
        "logger.info('%d accounts', len(usernames))",
    ],
)
def test_the_check_passes_a_label_or_a_name_outside_a_log(source):
    assert _log_leaks(source) == []
