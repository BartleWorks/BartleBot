"""A process pool's worker ends when the process that started it does."""

import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from bartlebot.combat import dsl_check_worker

_REPO = Path(__file__).resolve().parents[2]
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0


def _sleeper() -> subprocess.Popen:
    """A process that lives until its stdin closes or it is killed."""
    return subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)


def _recorder() -> tuple[threading.Event, list[int], Callable[[int], None]]:
    """A stand-in for os._exit, which would end the test run."""
    ended, codes = threading.Event(), []

    def _exit(code: int) -> None:
        codes.append(code)
        ended.set()

    return ended, codes, _exit


def test_the_child_ends_when_its_parent_does():
    parent = _sleeper()
    ended, codes, _exit = _recorder()
    try:
        dsl_check_worker.exit_with_parent(parent.pid, _exit)
        assert not ended.wait(0.1)
    finally:
        parent.kill()
        parent.wait()
    assert ended.wait(5)
    assert codes == [0]


def test_a_parent_already_gone_ends_the_child_at_once():
    parent = subprocess.Popen([sys.executable, "-c", "pass"])
    parent.wait()
    ended, codes, _exit = _recorder()

    dsl_check_worker.exit_with_parent(parent.pid, _exit)

    assert ended.wait(5)
    assert codes == [0]


# The app's own quit: a pool started with the watch, and the parent ending itself with
# TerminateProcess, so concurrent.futures never tells its worker to leave.
_PARENT = """
import ctypes, multiprocessing, os, sys
from concurrent.futures import ProcessPoolExecutor
from bartlebot.combat.dsl_check_worker import exit_with_parent

if __name__ == "__main__":
    pool = ProcessPoolExecutor(
        max_workers=1,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=exit_with_parent,
        initargs=(os.getpid(),),
    )
    with open(sys.argv[1], "w", encoding="utf-8") as out:
        out.write(str(pool.submit(os.getpid).result()))
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.TerminateProcess(kernel32.GetCurrentProcess(), 0)
"""


def _wait_for_exit(pid: int, seconds: float) -> bool:
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        return True
    try:
        return kernel32.WaitForSingleObject(handle, int(seconds * 1000)) == _WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def test_a_pool_worker_ends_when_the_app_terminates_itself(tmp_path):
    script, pid_file, errors = tmp_path / "parent.py", tmp_path / "worker.pid", tmp_path / "stderr.txt"
    script.write_text(_PARENT, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(_REPO)}
    # Files, never pipes: a surviving worker inherits the parent's pipes, and run() would
    # then wait on them for as long as it lives, turning the failure into a hang.
    with errors.open("w", encoding="utf-8") as stderr:
        subprocess.run(
            [sys.executable, str(script), str(pid_file)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            timeout=60,
        )
    assert pid_file.exists(), errors.read_text(encoding="utf-8")
    worker = int(pid_file.read_text(encoding="utf-8"))

    exited = _wait_for_exit(worker, 10)
    if not exited:
        # Still running, so the id is still the worker's: safe to kill it by number.
        subprocess.run(["taskkill", "/F", "/PID", str(worker)], capture_output=True)
    assert exited, "the worker outlived its parent"
