import sys
import asyncio
import traceback

from PyQt6.QtWidgets import QApplication
import qasync

from wizwalker import ClientHandler

from bartlebot.bot.machine import BotMachine
from bartlebot.gui.main_window import MainWindow
from bartlebot.nodes.registry import NODE_REGISTRY

# ---------------------------------------------------------------------------
# MVP bot config — two-state zone loop.
# Fill in real zone strings and XYZ coordinates before running.
# ---------------------------------------------------------------------------

MVP_BOT_CONFIG = {
    "initial": "go_to_zone_a",
    "states": [
        {
            "id": "go_to_zone_a",
            "node_type": "go_to_zone",
            "params": {
                "from_zone": "WizardCity/WC_Streets/WC_Unicorn",
                "to_zone": "WizardCity/WC_Streets/Interiors/WC_Unicorn_H3",
                "xyz": {"x": -12750.010, "y": 6295.667, "z": -119.930},
            },
        },
        {
            "id": "go_to_zone_b",
            "node_type": "go_to_zone",
            "params": {
                "from_zone": "WizardCity/WC_Streets/Interiors/WC_Unicorn_H3",
                "to_zone": "WizardCity/WC_Streets/WC_Unicorn",
                "xyz": {"x": 637.394, "y": 29.265, "z": 1.974},
            },
        },
    ],
    "transitions": [
        # Success: advance to the other zone.
        {
            "trigger": "advance",
            "source": "go_to_zone_a",
            "dest": "go_to_zone_b",
            "conditions": ["last_result_is_success"],
        },
        {
            "trigger": "advance",
            "source": "go_to_zone_b",
            "dest": "go_to_zone_a",
            "conditions": ["last_result_is_success"],
        },
        # Failure: retry the same state.
        {"trigger": "advance", "source": "go_to_zone_a", "dest": "go_to_zone_a"},
        {"trigger": "advance", "source": "go_to_zone_b", "dest": "go_to_zone_b"},
    ],
}


# ---------------------------------------------------------------------------
# Client management
# ---------------------------------------------------------------------------
_handler = ClientHandler()
_clients: list = []
_current_machine: BotMachine | None = None
_bot_task: asyncio.Task | None = None


async def client_loop(window: MainWindow) -> None:
    """Continuously monitors for hooked clients and updates the zone label."""
    global _clients

    while True:
        try:
            clients = _handler.get_new_clients()
            if not clients:
                window.set_zone("No client found")
                _clients = []
                window.set_start_enabled(False)
                await asyncio.sleep(3.0)
                continue

            await asyncio.gather(*[c.activate_hooks() for c in clients])
            _clients = clients
            window.set_start_enabled(True)

            while True:
                zone = await clients[0].zone_name()
                window.set_zone(zone)
                await asyncio.sleep(1.0)

        except Exception:
            _clients = []
            window.set_start_enabled(False)
            window.set_zone("Error — retrying…")
            print(traceback.format_exc())
            await asyncio.sleep(3.0)


# ---------------------------------------------------------------------------
# Bot start / stop
# ---------------------------------------------------------------------------
async def start_bot(window: MainWindow) -> None:
    global _current_machine, _bot_task

    if not _clients:
        return

    machine = BotMachine(
        clients=_clients,
        config=MVP_BOT_CONFIG,
        node_registry=NODE_REGISTRY,
        on_state_change=window.set_fsm_state,
    )
    _current_machine = machine

    window.set_start_enabled(False)
    window.set_stop_enabled(True)
    window.set_fsm_state(MVP_BOT_CONFIG["initial"])

    try:
        await machine.run()
    finally:
        _current_machine = None
        window.set_fsm_state("Idle")
        window.set_stop_enabled(False)
        window.set_start_enabled(bool(_clients))


def stop_bot() -> None:
    if _current_machine:
        _current_machine.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main(app: QApplication) -> None:
    window = MainWindow()
    window.show()

    window.start_button.clicked.connect(
        lambda: asyncio.create_task(start_bot(window))
    )
    window.stop_button.clicked.connect(stop_bot)

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
