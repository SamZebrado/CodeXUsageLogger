import ast
import pathlib
import unittest
from codex_quota_logger.protocol import ALLOWED_REQUESTS, ALLOWED_NOTIFICATIONS

ROOT = pathlib.Path(__file__).resolve().parents[1]

class StaticSafetyTests(unittest.TestCase):
    def test_exact_rpc_allowlist(self):
        self.assertEqual(ALLOWED_REQUESTS, {"initialize", "account/read", "account/rateLimits/read", "account/usage/read"})
        self.assertEqual(ALLOWED_NOTIFICATIONS, {"initialized"})
    def test_no_shell_eval_exec(self):
        for p in (ROOT/"codex_quota_logger").glob("*.py"):
            for n in ast.walk(ast.parse(p.read_text())):
                if isinstance(n, ast.Call):
                    for kw in n.keywords:
                        self.assertFalse(kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True, p)
                    if isinstance(n.func, ast.Name): self.assertNotIn(n.func.id, {"eval", "exec"}, p)
    def test_constant_rpc_calls_are_read_only(self):
        count = 0
        for p in (ROOT/"codex_quota_logger").glob("*.py"):
            for n in ast.walk(ast.parse(p.read_text())):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "request":
                    self.assertTrue(n.args and isinstance(n.args[0], ast.Constant), p)
                    self.assertIn(n.args[0].value, ALLOWED_REQUESTS, p)
                    count += 1
        self.assertEqual(count, 4)
    def test_no_direct_network_or_auth_file_access(self):
        for p in (ROOT/"codex_quota_logger").glob("*.py"):
            text = p.read_text()
            for denied in ("auth.json", "Authorization", "urlopen(", "requests.get(", "requests.post(", "socket.connect(", "codex exec"):
                self.assertNotIn(denied, text, p)
    def test_runtime_secrets_gitignored(self):
        text = (ROOT/".gitignore").read_text()
        for item in ("identity.salt", "quota_history.csv", "health.json", "auth.json", "raw/"):
            self.assertIn(item, text)
