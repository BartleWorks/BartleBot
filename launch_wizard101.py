import asyncio
import sys

from wizwalker.utils import get_all_wizard_handles, instance_login, start_instance, set_window_title
from wizwalker.client import Client

if len(sys.argv) != 2:
    print("Usage: python launch_wizard101.py username:password")
    sys.exit(1)

username, password = sys.argv[1].split(":")


async def main():
    start_handles = set(get_all_wizard_handles())
    start_instance()

    while len(set(get_all_wizard_handles()).difference(start_handles)) != 1:
        await asyncio.sleep(0.1)

    handle = set(get_all_wizard_handles()).difference(start_handles).pop()
    client = Client(handle)

    await client.hook_handler.activate_root_window_hook()

    while await client.root_window.get_windows_with_name("LoginWindow") == []:
        await asyncio.sleep(0.1)

    instance_login(handle, username, password)
    set_window_title(handle, f"[{username}] Wizard101")
    await client.hook_handler.close()


if __name__ == "__main__":
    asyncio.run(main())
