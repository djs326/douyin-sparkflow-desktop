"""协议发送器安全收敛测试：codeGeneration 纵深（H5）、bundle 完整性（H5）、代理环境变量（M2）。

威胁模型说明：Node 官方声明 vm 不是安全边界——注入外层对象即存在逃逸路径，
代码可信性由 sha256 manifest（加载内容 == 抖音官方 CDN 基线，SRI 语义）保证；
vm.createContext(codeGeneration) 禁用沙箱内 eval/new Function 作为纵深。
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import protocol_dispatch

MJS_PATH = Path(__file__).resolve().parents[1] / "core" / "protocol_sender.mjs"
PY_DISPATCH_PATH = Path(__file__).resolve().parents[1] / "core" / "protocol_dispatch.py"

# 与 protocol_sender.mjs 等价的沙箱执行配置（createContext + codeGeneration options）
SANDBOX_BOOTSTRAP = """
import vm from "node:vm";
const context = { Buffer, Blob, URL, URLSearchParams, TextDecoder, TextEncoder, crypto, setTimeout };
context.globalThis = context;
const contextified = vm.createContext(context, { codeGeneration: { strings: false, wasm: false } });
"""


def _node_available():
    return shutil.which("node") is not None


@unittest.skipUnless(_node_available(), "Node.js is not available")
class ProtocolSandboxCodeGenTests(unittest.TestCase):
    """H5 纵深：createContext(options) 必须真正禁用沙箱内 eval/new Function。"""

    def _run_node(self, script):
        code = SANDBOX_BOOTSTRAP + script
        result = subprocess.run(
            ["node", "--input-type=module", "-e", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def test_new_function_is_blocked_in_sandbox(self):
        output = self._run_node(
            """
            try {
              vm.runInContext("new Function('return 1')", contextified);
              console.log("ALLOWED");
            } catch (e) {
              console.log("BLOCKED:" + e.name);
            }
            """
        )
        self.assertIn("BLOCKED", output)
        self.assertNotIn("ALLOWED", output)

    def test_eval_is_blocked_in_sandbox(self):
        output = self._run_node(
            """
            try {
              vm.runInContext("eval('1+1')", contextified);
              console.log("ALLOWED");
            } catch (e) {
              console.log("BLOCKED:" + e.name);
            }
            """
        )
        self.assertIn("BLOCKED", output)
        self.assertNotIn("ALLOWED", output)

    def test_sandbox_has_no_process_global(self):
        output = self._run_node(
            'console.log("PROCESS_TYPE:" + vm.runInContext("typeof process", contextified));'
        )
        self.assertEqual("PROCESS_TYPE:undefined", output)


@unittest.skipUnless(_node_available(), "Node.js is not available")
class ProtocolSdkFunctionalityTests(unittest.TestCase):
    """SDK 功能回归防护：真实抖音 bundle 大量使用 new Blob/new URL/instanceof（主包 7771 实测
    new Blob×7、new URL×9、instanceof×8），沙箱配置不得破坏这些规范构造语义。"""

    def _run_node(self, script):
        code = SANDBOX_BOOTSTRAP + script
        result = subprocess.run(
            ["node", "--input-type=module", "-e", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def test_new_blob_still_works(self):
        output = self._run_node(
            'console.log("BLOB_OK:" + (new Blob(["a"]).size));'
        )
        self.assertEqual("BLOB_OK:1", output)

    def test_new_url_still_works(self):
        output = self._run_node(
            'console.log("URL_OK:" + (new URL("https://creator.douyin.com/").hostname));'
        )
        self.assertEqual("URL_OK:creator.douyin.com", output)

    def test_instanceof_still_works(self):
        output = self._run_node(
            'console.log("INSTANCE_OK:" + (new URLSearchParams("a=1") instanceof URLSearchParams));'
        )
        self.assertEqual("INSTANCE_OK:true", output)

    def test_crypto_get_random_values_still_works(self):
        output = self._run_node(
            'const arr = new Uint8Array(4); crypto.getRandomValues(arr); console.log("CRYPTO_OK:" + (arr.length === 4));'
        )
        self.assertEqual("CRYPTO_OK:true", output)

    def test_set_timeout_still_works(self):
        output = self._run_node(
            """
            let fired = false;
            setTimeout(() => { fired = true; }, 5);
            setTimeout(() => { console.log("TIMER_OK:" + fired); }, 30);
            """
        )
        self.assertEqual("TIMER_OK:true", output)


class ProtocolIntegrityContractTests(unittest.TestCase):
    """H5：源码契约——完整性校验与 codeGeneration 纵深要素必须存在。"""

    def test_mjs_uses_create_context_with_code_generation_options(self):
        source = MJS_PATH.read_text(encoding="utf-8")
        self.assertIn("vm.createContext", source)
        self.assertIn("codeGeneration", source)
        self.assertIn("strings: false", source)

    def test_mjs_has_escape_hatch_env(self):
        source = MJS_PATH.read_text(encoding="utf-8")
        self.assertIn("SPARKFLOW_PROTOCOL_ALLOW_CODE_GEN", source)

    def test_mjs_has_sha256_manifest(self):
        source = MJS_PATH.read_text(encoding="utf-8")
        self.assertIn("MANIFEST_FILENAME", source)
        self.assertIn("sha256Hex", source)
        self.assertIn("loadManifest", source)
        self.assertIn("saveManifest", source)

    def test_python_sets_proxy_env(self):
        source = PY_DISPATCH_PATH.read_text(encoding="utf-8")
        self.assertIn("NODE_USE_ENV_PROXY", source)
        self.assertIn("HTTPS_PROXY", source)


class ProtocolProxyEnvTests(unittest.TestCase):
    """M2：配置了代理时，node 子进程收到 NODE_USE_ENV_PROXY + 代理环境变量。"""

    def _run_with_proxy(self, proxy):
        completed = Mock()
        completed.returncode = 0
        completed.stdout = json.dumps({"ok": True, "userId": "1", "resolved": [], "unresolved": [], "sent": []})
        completed.stderr = ""
        with (
            patch.object(
                protocol_dispatch,
                "_build_protocol_command",
                return_value=(["node", "protocol_sender.mjs"], Path("."), "local-node", str(Path("."))),
            ),
            patch.object(protocol_dispatch.subprocess, "run", return_value=completed) as run_mock,
        ):
            protocol_dispatch._run_protocol_for_user(
                {"username": "demo", "cookies": [], "targets": []},
                {},
                True,
                {"messageIntervalSecondsMin": 0, "messageIntervalSecondsMax": 0},
                proxy,
            )
        return run_mock.call_args.kwargs.get("env", {})

    def test_proxy_env_set_when_proxy_configured(self):
        env = self._run_with_proxy("http://127.0.0.1:7890")
        self.assertEqual("1", env.get("NODE_USE_ENV_PROXY"))
        self.assertEqual("http://127.0.0.1:7890", env.get("HTTPS_PROXY"))
        self.assertEqual("http://127.0.0.1:7890", env.get("HTTP_PROXY"))
        self.assertIn("127.0.0.1", env.get("NO_PROXY", ""))

    def test_no_proxy_env_when_proxy_empty(self):
        env = self._run_with_proxy("")
        self.assertNotIn("NODE_USE_ENV_PROXY", env)
        self.assertNotIn("HTTPS_PROXY", env)

    def test_payload_includes_proxy_field(self):
        completed = Mock()
        completed.returncode = 0
        completed.stdout = json.dumps({"ok": True, "userId": "1", "resolved": [], "unresolved": [], "sent": []})
        completed.stderr = ""
        with (
            patch.object(
                protocol_dispatch,
                "_build_protocol_command",
                return_value=(["node", "protocol_sender.mjs"], Path("."), "local-node", str(Path("."))),
            ),
            patch.object(protocol_dispatch.subprocess, "run", return_value=completed) as run_mock,
        ):
            protocol_dispatch._run_protocol_for_user(
                {"username": "demo", "cookies": [], "targets": []},
                {},
                True,
                {"messageIntervalSecondsMin": 0, "messageIntervalSecondsMax": 0},
                "http://127.0.0.1:7890",
            )
        payload = json.loads(run_mock.call_args.kwargs["input"])
        self.assertEqual("http://127.0.0.1:7890", payload["proxy"])


if __name__ == "__main__":
    unittest.main()
