"""One BartleBot per user: a later launch hands its arguments over and exits (#27)."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid

import pytest
from PyQt6.QtNetwork import QLocalSocket
from PyQt6.QtWidgets import QWidget

from bartlebot.gui import single_instance
from bartlebot.gui.single_instance import LaunchQueue, SingleInstance, server_name


@pytest.fixture
def name():
    return f"Bartleby.BartleBot.test-{uuid.uuid4().hex}"


#: A later launch, as ``bartlebot.py`` makes one: its own process, exiting 3 when it handed over.
_LATER_LAUNCH = """
import sys
from PyQt6.QtCore import QCoreApplication
from bartlebot.gui.single_instance import SingleInstance
app = QCoreApplication(sys.argv[:1])
sys.exit(0 if SingleInstance(sys.argv[1]).claim(sys.argv[2:]) else 3)
"""


def _later_launch(name: str, *args: str, cwd=None) -> subprocess.Popen:
    """Start a later launch in its own process; the running one must keep its loop turning meanwhile."""
    return subprocess.Popen([sys.executable, "-c", _LATER_LAUNCH, name, *args], cwd=cwd)


def _handed_over(qtbot, launch: subprocess.Popen) -> bool:
    qtbot.waitUntil(lambda: launch.poll() is not None, timeout=30000)
    return launch.returncode == 3


@pytest.fixture
def first(qapp, name):
    instance = SingleInstance(name)
    yield instance
    instance.close()


def test_the_first_launch_runs(first):
    assert first.claim([]) is True
    assert first.is_listening


def test_a_later_launch_hands_its_arguments_over_and_does_not_run(qtbot, first, name, tmp_path):
    share = tmp_path / "Gift.bbot"
    share.write_text("{}", encoding="utf-8")
    first.claim([])

    with qtbot.waitSignal(first.arguments_received, timeout=30000) as received:
        launch = _later_launch(name, "Gift.bbot", "--flag", cwd=tmp_path)
    assert _handed_over(qtbot, launch)

    assert received.args == [[str(share.resolve()), "--flag"]]


def test_a_relative_path_is_made_absolute_before_it_is_sent(monkeypatch, tmp_path):
    (tmp_path / "Gift.bbot").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert single_instance.absolute_arguments(["Gift.bbot", "--x"]) == [str(tmp_path / "Gift.bbot"), "--x"]


def test_two_later_launches_each_arrive(qtbot, first, name):
    first.claim([])
    got = []
    first.arguments_received.connect(got.append)

    launches = [_later_launch(name, "a"), _later_launch(name, "b")]
    assert all(_handed_over(qtbot, launch) for launch in launches)
    qtbot.waitUntil(lambda: len(got) == 2, timeout=5000)

    assert sorted(got) == [["a"], ["b"]]


@pytest.mark.parametrize("payload", [b"not json", json.dumps({"a": 1}).encode(), json.dumps([1, 2]).encode()])
def test_something_other_than_arguments_is_ignored(qtbot, first, name, payload):
    first.claim([])
    got = []
    first.arguments_received.connect(got.append)
    socket = QLocalSocket()
    socket.connectToServer(name)
    assert socket.waitForConnected(2000)
    socket.write(payload)
    socket.flush()
    qtbot.wait(50)
    socket.disconnectFromServer()
    qtbot.wait(200)

    assert got == []


def test_once_the_first_has_closed_the_next_launch_runs(first, name):
    first.claim([])
    first.close()

    second = SingleInstance(name)
    assert second.claim([]) is True
    second.close()


def test_the_name_is_per_user_and_says_nothing_of_who():
    a = server_name(packaged=True, dev=False, user="alice")
    b = server_name(packaged=True, dev=False, user="bob")

    assert a != b
    assert a.startswith("Bartleby.BartleBot.")
    assert "alice" not in a


def test_a_checkout_never_shares_a_name_with_the_installed_app():
    """Done when (#237): a checkout under ``BARTLEBOT_DEV`` never hands its arguments to an installed app."""
    installed = server_name(packaged=True, dev=False, user="u")

    assert (
        len(
            {
                installed,
                server_name(packaged=False, dev=False, user="u"),
                server_name(packaged=False, dev=True, user="u"),
            }
        )
        == 3
    )


def test_the_default_name_follows_the_dev_switch(monkeypatch):
    monkeypatch.setenv("BARTLEBOT_DEV", "1")
    dev = server_name()
    monkeypatch.delenv("BARTLEBOT_DEV")

    assert dev == server_name() + ".dev"


def test_a_checkout_under_dev_does_not_reach_an_installed_app(qtbot, qapp):
    user = f"test-{uuid.uuid4().hex}"
    installed = SingleInstance(server_name(packaged=True, dev=False, user=user))
    installed.claim([])
    got = []
    installed.arguments_received.connect(got.append)

    try:
        launch = _later_launch(server_name(packaged=False, dev=True, user=user), "Gift.bbot")
        assert not _handed_over(qtbot, launch)
        assert got == []
    finally:
        installed.close()


def test_the_queue_releases_at_once_when_the_app_is_free(qtbot, qapp):
    queue = LaunchQueue(lambda: False)

    with qtbot.waitSignal(queue.ready, timeout=1000) as released:
        queue.put(["a.bbot"])

    assert released.args == [["a.bbot"]]


def test_the_queue_waits_for_a_modal_dialog_to_close(qtbot, qapp, monkeypatch):
    monkeypatch.setattr(single_instance, "BUSY_RETRY_MS", 10)
    busy = [True]
    queue = LaunchQueue(lambda: busy[0])
    got = []
    queue.ready.connect(got.append)

    queue.put(["a.bbot"])
    queue.put(["b.bbot"])
    qtbot.wait(60)
    assert got == []

    busy[0] = False
    qtbot.waitUntil(lambda: bool(got), timeout=1000)
    assert got == [["a.bbot", "b.bbot"]]


def test_raising_restores_a_minimized_window(qapp):
    window = QWidget()
    window.showMinimized()

    single_instance.raise_window(window)

    assert not window.isMinimized()
