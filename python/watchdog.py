import os
import sys
import threading
import time


def parent_alive() -> bool:
    ppid = int(os.environ.get("INKCUBE_PARENT_PID", "0"))
    if ppid <= 0:
        return True
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, ppid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(ppid, 0)
            return True
        except ProcessLookupError:
            return False


def start_watchdog(interval: float = 5.0):
    def _loop():
        while True:
            time.sleep(interval)
            if not parent_alive():
                print("[watchdog] Parent process gone, shutting down", flush=True)
                os._exit(0)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
