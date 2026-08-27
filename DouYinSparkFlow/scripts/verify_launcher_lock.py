"""验证 launcher 单实例锁的 PID 存活探测（修复后）。

用法（在项目目录的 PowerShell 里）：
    cd C:\Users\Lanxi\Desktop\douyin-sparkflow-desktop\DouYinSparkFlow
    .\.venv\Scripts\python.exe scripts\verify_launcher_lock.py

期望输出全部 PASS。修复前：对已死 PID 会抛异常（导致程序静默退出、双击没反应）。
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
