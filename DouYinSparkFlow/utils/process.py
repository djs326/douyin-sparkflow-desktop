"""跨平台安全的进程存活探测工具。

Windows 上 ``os.kill(pid, 0)`` 的 sig=0 恰好等于 ``CTRL_C_EVENT``，
Python 会对该值走 ``GenerateConsoleCtrlEvent`` 分支，向与 pid 共享
同一控制台的所有进程广播 Ctrl+C（杀死调用方及兄弟进程），绝不能
用于存活探测。详见仓库根目录 ``OSKILL-PROBE-BUG.md``。

Windows 改用 ``OpenProcess`` + ``GetExitCodeProcess``（判断 STILL_ACTIVE），
其他平台保留 ``os.kill(pid, 0)``。
"""

import ctypes
import os

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def pid_is_alive(pid):
    """探测进程是否存活（无副作用，不会向进程发送任何信号）。

    :param pid: 目标进程 ID；None 或非正数一律视为不存在
    :return: 存活返回 True；无法确认时保守返回 False（由上层兜底逻辑决策）
    """
    if pid is None or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
