"""Tie the Windows server process to the visible bootstrap console."""
from __future__ import annotations
import ctypes
import os
import threading

_handlers = []  # Keep native callback wrappers alive for the process lifetime.

def install_console_lifetime() -> None:
    if os.name != "nt":
        return
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    kernel.SetConsoleCtrlHandler.argtypes = [callback_type, wintypes.BOOL]
    kernel.SetConsoleCtrlHandler.restype = wintypes.BOOL
    @callback_type
    def on_control(event):
        if event in (2, 5, 6):  # Close, logoff, shutdown: never wait for a GPU step.
            os._exit(130)
        return False  # Preserve ordinary Ctrl+C handling.
    if not kernel.SetConsoleCtrlHandler(on_control, True):
        raise ctypes.WinError(ctypes.get_last_error())
    _handlers.append(on_control)
    owner = os.environ.get("CVELD_STUDIO_OWNER_PID")
    if not owner:
        return
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, int(owner))  # SYNCHRONIZE only.
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    def watch_owner():
        result = kernel.WaitForSingleObject(handle, 0xffffffff)
        kernel.CloseHandle(handle)
        if result == 0:
            os._exit(130)
    threading.Thread(target=watch_owner, name="studio-console-owner", daemon=True).start()
