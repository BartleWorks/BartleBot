import asyncio
import ctypes
import subprocess
from typing import Callable, List, Dict, Tuple

from .patcher import get_game_path
from .enums import GameClass, GameExecutable, Game

user32 = ctypes.windll.user32


def start_wiz_instance():
    """
    Starts a wizard101 instance
    """
    location = get_game_path(Game.WIZARD101)
    subprocess.Popen(
        rf"{location}\Bin\WizardGraphicalClient.exe -L login.us.wizard101.com 12000",
        cwd=rf"{location}\Bin",
    )


def get_windows_from_predicate(predicate: Callable) -> list:
    handles = []

    def callback(handle, _):
        if predicate(handle):
            handles.append(handle)

        # iterate all windows, (True)
        return 1

    enumwindows_func_type = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    )

    callback = enumwindows_func_type(callback)
    user32.EnumWindows(callback, 0)

    return handles


def get_all_handles_with_name(name: str) -> list:
    """
    Get handles to all currently open game clients
    """

    def callback(handle):
        class_name = ctypes.create_unicode_buffer(len(name))
        user32.GetClassNameW(handle, class_name, len(name) + 1)
        if name == class_name.value:
            return True

    return get_windows_from_predicate(callback)


def get_window_title(hwnd):
    GetWindowText = ctypes.windll.user32.GetWindowTextW
    title_length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    title_buffer = ctypes.create_unicode_buffer(title_length + 1)
    GetWindowText(hwnd, title_buffer, title_length + 1)
    return title_buffer.value


def send_chars(window_handle, chars: str):
    for char in chars:
        user32.SendMessageW(window_handle, 0x102, ord(char), 0)


async def login_client(handle, login: Tuple[str, Dict[str, str]], game: Game = Game.WIZARD101, set_title: bool = True):
    await asyncio.sleep(0)
    nickname, dict = login

    send_chars(handle, dict["username"])
    await asyncio.sleep(0)
    # tab
    user32.SendMessageW(handle, 0x102, 9, 0)
    send_chars(handle, dict["password"])
    await asyncio.sleep(0)
    # enter
    user32.SendMessageW(handle, 0x102, 13, 0)

    if set_title:
        # Set title
        user32.SetWindowTextW(handle, f"[{nickname}] {game.name.capitalize()}")


async def launch_instances(logins: List[Tuple[str, Dict[str, str]]], game: Game = Game.WIZARD101, gamepath: str = get_game_path(Game.WIZARD101)):
    start_handles = set(get_all_handles_with_name(GameClass[game.name].value))

    for _ in logins:
        subprocess.Popen(
            rf"{gamepath}Bin/{GameExecutable[game.name].value}.exe -L login.us.{game.name}.com 12000",
            cwd=rf"{gamepath}/Bin",
        )

    while len(get_all_handles_with_name(GameClass[game.name].value)) < len(start_handles) + len(logins):
        await asyncio.sleep(0.1)

    new_handles = set(get_all_handles_with_name(GameClass[game.name].value)).difference(start_handles)

    async def _wait_for_window_load(handle, title: str = game.name.capitalize()):
        while get_window_title(handle) != title:
            await asyncio.sleep(0.1)

    await asyncio.gather(*[_wait_for_window_load(h) for h in new_handles])
    await asyncio.sleep(1.5) #we do a little race condition
    await asyncio.gather(*[login_client(h, l, game) for h, l in zip(new_handles, logins)])
    if game == Game.PIRATE101:
        await asyncio.gather(*[_wait_for_window_load(h) for h in new_handles])
        await asyncio.gather(*[login_client(h, l, game) for h, l in zip(new_handles, logins)]) #TODO: Dont have this log in twice you fuckwit