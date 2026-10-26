"""Windows process-instance/window identity without titles or content."""

import ctypes
import os
from ctypes import wintypes


def window_identity(pid: int, window_id: int) -> tuple[int, int]:
    if os.name != "nt":
        raise ValueError("Computer control requires Windows.")
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.IsWindow.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    actual = wintypes.DWORD()
    if not user.IsWindow(window_id):
        raise ValueError("Stale window. Discover the app again.")
    user.GetWindowThreadProcessId(window_id, ctypes.byref(actual))
    if actual.value != pid:
        raise ValueError("Stale process/window identity. Discover again.")
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ValueError("Cannot verify this process identity.")
    stamps = [wintypes.FILETIME() for _ in range(4)]
    try:
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(s) for s in stamps)):
            raise ValueError("Cannot verify this process instance.")
        return pid, (stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)
