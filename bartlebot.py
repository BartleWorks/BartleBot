import sys
import asyncio
import traceback

from PyQt6.QtWidgets import QApplication
import qasync

from wizwalker import ClientHandler

from bartlebot.gui.main_window import MainWindow


async def zone_loop(window: MainWindow) -> None:
    while True:
        handler = ClientHandler()
        try:
            clients = handler.get_new_clients()
            if not clients:
                window.set_zone("No client found")
                await asyncio.sleep(3.0)
                continue

            client = clients[0]
            await client.activate_hooks()

            while True:
                zone = await client.zone_name()
                window.set_zone(zone)
                await asyncio.sleep(1.0)

        except Exception:
            window.set_zone("Error — retrying…")
            print(traceback.format_exc())
            await asyncio.sleep(3.0)
        finally:
            await handler.close()


async def main(app):
    window = MainWindow()
    window.show()

    asyncio.create_task(zone_loop(window))

    quit_event = asyncio.Event()
    app.aboutToQuit.connect(quit_event.set)

    await quit_event.wait()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        loop.run_until_complete(main(app))