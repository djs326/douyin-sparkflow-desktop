"""打包版隐藏控制台窗口修复测试：subprocess 调用点必须携带 SW_HIDE startupinfo。

背景：打包版（PyInstaller windowed）主进程无控制台，从它 spawn 的 console 子系统
子进程（icacls/node/taskkill/docker 等）会被 Windows 分配新控制台窗口——用户看到
"黑色控制台闪现"。开发版（python.exe 有控制台）子进程继承控制台，无此问题。
"""

import os
import subprocess
import unittest
from pathlib import Path

from utils.process import hidden_startupinfo

ROOT = Path(__file__).resolve().parents[1]

# 打包版 GUI 进程会触发的 subprocess 调用文件
TARGET_FILES = [
    ROOT / "utils" / "config.py",
    ROOT / "webui" / "ops.py",
    ROOT / "core" / "protocol_dispatch.py",
    ROOT / "core" / "browser.py",
]


@unittest.skipUnless(os.name == "nt", "Windows only")
class HiddenStartupInfoTests(unittest.TestCase):
    """hidden_startupinfo 在 Windows 返回带 SW_HIDE 的 STARTUPINFO。"""

    def test_returns_startupinfo_with_sw_hide(self):
        startupinfo = hidden_startupinfo()
        self.assertIsNotNone(startupinfo)
        self.assertTrue(startupinfo.dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(subprocess.SW_HIDE, startupinfo.wShowWindow)


class SubprocessCallSiteContractTests(unittest.TestCase):
    """源码契约：打包版 GUI 进程可达的 subprocess 调用文件必须使用隐藏 startupinfo。"""

    def test_call_sites_use_hidden_startupinfo(self):
        for file_path in TARGET_FILES:
            content = file_path.read_text(encoding="utf-8")
            self.assertIn(
                "startupinfo=hidden_startupinfo()",
                content,
                f"{file_path.name} 缺少 startupinfo=hidden_startupinfo()",
            )
            self.assertIn(
                "from utils.process import hidden_startupinfo",
                content,
                f"{file_path.name} 缺少 hidden_startupinfo 导入",
            )


if __name__ == "__main__":
    unittest.main()
