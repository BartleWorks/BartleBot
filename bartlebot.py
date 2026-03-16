import sys
import asyncio
import traceback
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
import qasync

from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.gui.main_window import MainWindow
from bartlebot.nodes.registry import NODE_REGISTRY
from bartlebot import recent_files


# ---------------------------------------------------------------------------
# Client management
# ---------------------------------------------------------------------------
_handler = WizSprinter()
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
            # Clean up any clients whose process has exited
            _handler.remove_dead_clients()

            new_clients = _handler.get_new_clients()
            if new_clients:
                try:
                    await asyncio.gather(*[c.activate_hooks() for c in new_clients])
                except Exception:
                    # Hook activation failed (e.g. client still initialising).
                    # Unregister the failed clients so they're retried next cycle.
                    for c in new_clients:
                        if c in _handler.clients:
                            _handler.clients.remove(c)
                        if c.window_handle in _handler._managed_handles:
                            _handler._managed_handles.remove(c.window_handle)
                    print(traceback.format_exc())
                    await asyncio.sleep(3.0)
                    continue

            clients = list(_handler.clients)
            if not clients:
                window.set_zone("No client found")
                _clients = []
                window.right_sidebar.clients.set_clients([])
                window.set_start_enabled(False)
                await asyncio.sleep(3.0)
                continue

            if clients != _clients:
                _clients = clients
                window.right_sidebar.clients.set_clients(clients)
                window.set_start_enabled(_start_enabled())

            zone = await clients[0].zone_name()
            window.set_zone(zone)
            await asyncio.sleep(1.0)

        except Exception:
            _clients = []
            window.right_sidebar.clients.set_clients([])
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
    window.left_sidebar.set_config(config)
    window.bot_canvas.setup(config)
    window.bot_canvas.enter_edit_mode()
    window.set_save_enabled(True)
    window.set_start_enabled(_start_enabled())


def open_bot(window: MainWindow, path: str | None = None) -> None:
    global _loaded_config, _last_save_path

    if path is None:
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
    window.left_sidebar.set_config(config)
    window.bot_canvas.setup(config)
    window.set_save_enabled(True)
    window.set_start_enabled(_start_enabled())
    window.update_recent_menu(recent_files.add(path))


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
        window.update_recent_menu(recent_files.add(path))
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
        bot_config=_loaded_config,
        on_state_change=window.set_fsm_state,
        on_transition=window.bot_canvas.on_transition,
        on_drop=window.right_sidebar.drops.add_drop,
        on_log=window.bot_canvas.append_log,
    )
    _current_machine = machine

    window.set_start_enabled(False)
    window.right_sidebar.drops.setup_clients(len(_clients))
    window.right_sidebar.drops.clear_drops()
    window.bot_canvas.set_running()

    try:
        await machine.run()
    finally:
        _current_machine = None
        window.bot_canvas.set_idle()
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

    def _config_changed() -> None:
        window.set_save_enabled(True)
        if _loaded_config is not None:
            window.left_sidebar.bot_info.refresh_states(_loaded_config.states)
            window.bot_canvas.refresh_initial(_loaded_config.initial)
            window.left_sidebar.options.set_config(_loaded_config)

    def _mode_changed(mode: BotMode) -> None:
        window.left_sidebar.set_editable(mode == BotMode.EDIT)
        if mode == BotMode.RUN:
            window.set_start_enabled(_start_enabled())

    def _save_and_done() -> None:
        save_bot(window)

    window.bot_canvas.on_config_changed = _config_changed
    window.bot_canvas.on_mode_changed = _mode_changed
    window.bot_canvas.on_save_and_done = _save_and_done
    window.bot_canvas.on_run = lambda: asyncio.create_task(start_bot(window))
    window.bot_canvas.on_pause = pause_bot
    window.bot_canvas.on_resume = resume_bot
    window.bot_canvas.stop_button.clicked.connect(stop_bot)
    window.bot_canvas.set_clients_getter(lambda: _clients)

    window.left_sidebar.on_config_changed = _config_changed
    window.on_open_recent = lambda path: open_bot(window, path)
    window.update_recent_menu(recent_files.load())

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
