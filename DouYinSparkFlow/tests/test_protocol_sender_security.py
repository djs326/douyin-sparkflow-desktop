"""协议发送器安全收敛测试：沙箱逃逸防护（H5）、bundle 完整性（H5）、代理环境变量（M2）。"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import protocol_dispatch

MJS_PATH = Path(__file__).resolve().parents[1] / "core" / "protocol_sender.mjs"
PY_DISPATCH_PATH = Path(__file__).resolve().parents[1] / "core" / "protocol_dispatch.py"

# 与 protocol_sender.mjs 中 sandboxSafe 等价的防护逻辑（测试沙箱逃逸是否被阻断）
SANDBOX_BOOTSTRAP = """
import vm from "node:vm";
const sandboxSafe = (value) => {
  if (value === null || (typeof value !== "function" && typeof value !== "object")) return value;
  return new Proxy(value, {
    get(target, prop, receiver) {
      if (prop === "constructor" || prop === "__proto__" || prop === "prototype") return undefined;
      return Reflect.get(target, prop, receiver);
    },
    has(target, prop) {
      if (prop === "constructor" || prop === "__proto__" || prop === "prototype") return false;
      return Reflect.has(target, prop);
    },
    getPrototypeOf() { return null; },
  });
};
const context = {
  Buffer: sandboxSafe(Buffer),
  console,
  setTimeout: sandboxSafe(setTimeout),
  crypto: sandboxSafe(crypto),
};
context[Symbol.for("nodejs.vm.codeGeneration")] = { strings: false, wasm: false };
"""


def _node_available():
    return shutil.which("node") is not None


@unittest.skipUnless(_node_available(), "Node.js is not available")
class ProtocolSandboxEscapadeTests(unittest.TestCase):
    """H5：vm 沙箱注入对象经 Proxy 包装 + codeGeneration 禁用后，典型逃逸手法必须被阻断。"""

    def _run_escape(self, script):
        code = SANDBOX_BOOTSTRAP + script
        result = subprocess.run(
            ["node", "--input-type=module", "-e", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def test_constructor_escape_is_blocked(self):
        output = self._run_escape(
            """
            try {
              const out = vm.runInNewContext("Buffer.constructor('return process')()", context);
              console.log("ESCAPED:" + String(out));
            } catch (e) {
              console.log("BLOCKED:" + e.name);
            }
            """
        )
        self.assertIn("BLOCKED", output)
        self.assertNotIn("ESCAPED", output)

    def test_prototype_chain_escape_is_blocked(self):
        output = self._run_escape(
            """
            try {
              const out = vm.runInNewContext(
                "Object.getPrototypeOf(Buffer).constructor('return process')()", context
              );
              console.log("ESCAPED:" + String(out));
            } catch (e) {
              console.log("BLOCKED:" + e.name);
            }
            """
        )
        self.assertIn("BLOCKED", output)
        self.assertNotIn("ESCAPED", output)

    def test_proto_escape_is_blocked(self):
        output = self._run_escape(
            """
            try {
              const out = vm.runInNewContext("Buffer.__proto__.constructor('return process')()", context);
              console.log("ESCAPED:" + String(out));
            } catch (e) {
              console.log("BLOCKED:" + e.name);
            }
            """
        )
        self.assertIn("BLOCKED", output)
        self.assertNotIn("ESCAPED", output)

    def test_sandbox_has_no_process_global(self):
        output = self._run_escape('console.log("PROCESS_TYPE:" + vm.runInNewContext("typeof process", context));')
        self.assertEqual("PROCESS_TYPE:undefined", output)

    def test_injected_function_still_usable_for_legit_calls(self):
        # Proxy 包装不应破坏正常调用（setTimeout 正常调度）
        output = self._run_escape(
            """
            let fired = false;
            setTimeout(() => { fired = true; }, 5);
            setTimeout(() => { console.log("FIRED:" + fired); }, 30);
            """
        )
        self.assertEqual("FIRED:true", output)


class ProtocolIntegrityContractTests(unittest.TestCase):
    """H5：源码契约——mjs/Python 侧必须包含完整性校验与逃逸防护要素。"""

    def test_mjs_disables_code_generation(self):
        source = MJS_PATH.read_text(encoding="utf-8")
        self.assertIn('Symbol.for("nodejs.vm.codeGeneration")', source)
        self.assertIn('strings: false', source)

    def test_mjs_wraps_injected_objects(self):
        source = MJS_PATH.read_text(encoding="utf-8")
        self.assertIn("sandboxSafe", source)
        self.assertIn("Buffer: sandboxSafe(Buffer)", source)

    def test_mjs_has_sha256_manifest(self):
        source = MJS_PATH.read_text(encoding="utf-8")
        self.assertIn("MANIFEST_FILENAME", source)
        self.assertIn("sha256Hex", source)
        self.assertIn("loadManifest", source)

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
