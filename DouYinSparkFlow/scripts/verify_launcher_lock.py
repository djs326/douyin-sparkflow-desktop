"""验证 launcher 单实例锁的 PID 存活探测（已用 OpenProcess+GetExitCodeProcess 修复）。

用法（在项目目录的 PowerShell 里）：
    cd C:\\Users\\Lanxi\\Desktop\\douyin-sparkflow-desktop\\DouYinSparkFlow
    .\\.venv\\Scripts\\python.exe scripts\\verify_launcher_lock.py

期望输出全部 PASS。修复前：Windows 上 os.kill(pid, 0) 的 sig=0 即 CTRL_C_EVENT，
"探测自己"会向同控制台广播 Ctrl+C、杀死运行本脚本的终端（见 OSKILL-PROBE-BUG.md）；
修复后探测自己无副作用，本脚本即回归验证。
"""

import os
import sys
from pathlib import Path

# 把 DouYinSparkFlow/ 加入 sys.path，以便 import launcher
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from launcher import _pid_is_alive  # noqa: E402

CHECKS = [
    ("dead pid (17576)", _pid_is_alive(17576), False),
    ("current process", _pid_is_alive(os.getpid()), True),
    ("invalid huge pid", _pid_is_alive(999999999), False),
]

ok = True
for name, got, want in CHECKS:
    passed = got == want
    ok = ok and passed
    print(f"[{'PASS' if passed else 'FAIL'}] {name}: got={got} want={want}")

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
