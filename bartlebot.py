import sys
import asyncio
import traceback
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
import qasync

from wizwalker import ClientHandler

from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.gui.main_window import MainWindow
from bartlebot.nodes.registry import NODE_REGISTRY


# ---------------------------------------------------------------------------
# Client management
# ---------------------------------------------------------------------------
_handler = ClientHandler()
_clients: list = []
_current_machine: BotMachine | None = None

# ---------------------------------------------------------------------------
# Loaded bot state
# ---------------------------------------------------------------------------
_loaded_config: BotConfig | None = None
_last_save_path: str | None = None


def _start_enabled() -> bool:
    """Start is only enabled when both a client is hooked and a bot is loaded."""
    return bool(_clients) and _loaded_config is not None


async def client_loop(window: MainWindow) -> None:
    """Continuously monitors for hooked clients and updates the sidebar."""
    global _clients

    while True:
        try:
            clients = _handler.get_new_clients()
            if not clients:
                window.set_zone("No client found")
                _clients = []
                window.client_sidebar.set_clients([])
                window.set_start_enabled(False)
                await asyncio.sleep(3.0)
                continue

            await asyncio.gather(*[c.activate_hooks() for c in clients])
            _clients = clients
            window.client_sidebar.set_clients(clients)
            window.set_start_enabled(_start_enabled())

            while True:
                zone = await clients[0].zone_name()
                window.set_zone(zone)
                await asyncio.sleep(1.0)

        except Exception:
            _clients = []
            window.client_sidebar.set_clients([])
            window.set_start_enabled(False)
            window.set_zone("Error — retrying…")
            print(traceback.format_exc())
            await asyncio.sleep(3.0)


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------
def new_bot(window: MainWindow) -> None:
    global _loaded_config, _last_save_path

    config = BotConfig(
        name="New Bot",
        initial="state_1",
        states=[StateModel(id="state_1", node_type="go_to_zone")],
        transitions=[],
    )
    _loaded_config = config
    _last_save_path = None
    window.set_bot_name(config.name, "Untitled")
    window.bot_info_panel.set_config(config)
    window.set_save_enabled(True)
    window.set_start_enabled(_start_enabled())


def open_bot(window: MainWindow) -> None:
    global _loaded_config, _last_save_path

    path, _ = QFileDialog.getOpenFileName(
        window, "Open Bot", "", "Bot Files (*.json)"
    )
    if not path:
        return

    try:
        config = BotConfig.load(path)
    except Exception as e:
        QMessageBox.critical(window, "Failed to load bot", str(e))
        return

    _loaded_config = config
    _last_save_path = path
    window.set_bot_name(config.name, Path(path).name)
    window.bot_info_panel.set_config(config)
    window.set_save_enabled(True)
    window.set_start_enabled(_start_enabled())


def save_bot(window: MainWindow) -> None:
    global _last_save_path

    if _loaded_config is None:
        return

    if _last_save_path is None:
        save_bot_as(window)
        return

    try:
        _loaded_config.save(_last_save_path)
    except Exception as e:
        QMessageBox.critical(window, "Failed to save bot", str(e))


def save_bot_as(window: MainWindow) -> None:
    global _last_save_path

    if _loaded_config is None:
        return

    path, _ = QFileDialog.getSaveFileName(
        window, "Save Bot As", "", "Bot Files (*.json)"
    )
    if not path:
        return

    if not path.endswith(".json"):
        path += ".json"

    try:
        _loaded_config.save(path)
        _last_save_path = path
        window.set_bot_name(_loaded_config.name, Path(path).name)
    except Exception as e:
        QMessageBox.critical(window, "Failed to save bot", str(e))


# ---------------------------------------------------------------------------
# Bot start / stop
# ---------------------------------------------------------------------------
async def start_bot(window: MainWindow) -> None:
    global _current_machine

    if not _clients or _loaded_config is None:
        return

    machine = BotMachine(
        clients=_clients,
        config=_loaded_config.to_machine_config(),
        node_registry=NODE_REGISTRY,
        on_state_change=window.set_fsm_state,
        on_transition=window.running_tab.on_transition,
    )
    _current_machine = machine

    window.set_start_enabled(False)
    window.switch_to_running_tab()
    window.drop_panel.setup_clients(len(_clients))
    window.drop_panel.clear()
    window.running_tab.setup_graph(_loaded_config.to_machine_config())
    window.running_tab.set_running()

    try:
        await machine.run()
    finally:
        _current_machine = None
        window.running_tab.set_idle()
        window.set_start_enabled(_start_enabled())


def pause_bot() -> None:
    if _current_machine:
        _current_machine.pause()


def resume_bot() -> None:
    if _current_machine:
        _current_machine.resume()


def stop_bot() -> None:
    if _current_machine:
        _current_machine.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main(app: QApplication) -> None:
    window = MainWindow()
    window.show()

    window.on_new_bot = lambda: new_bot(window)
    window.on_open_bot = lambda: open_bot(window)
    window.on_save_bot = lambda: save_bot(window)
    window.on_save_bot_as = lambda: save_bot_as(window)

    window.running_tab.on_run = lambda: asyncio.create_task(start_bot(window))
    window.running_tab.on_pause = pause_bot
    window.running_tab.on_resume = resume_bot
    window.running_tab.stop_button.clicked.connect(stop_bot)

    asyncio.create_task(client_loop(window))

    quit_event = asyncio.Event()
    app.aboutToQuit.connect(quit_event.set)
    await quit_event.wait()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        loop.run_until_complete(main(app))
