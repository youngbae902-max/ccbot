import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "ccbot.py"


class CCBotSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(SOURCE_PATH))

    def test_source_parses(self):
        self.assertIsNotNone(self.tree)

    def test_expected_admin_and_order_channel_are_configured(self):
        self.assertIn("ADMIN_ID = 1366109646934380614", self.source)
        self.assertIn("ORDERS_CHANNEL_ID = 1561513284191125625", self.source)

    def test_diagnostic_commands_are_moved_to_github_bridge(self):
        self.assertIn("import github_bridge", self.source)
        self.assertNotIn('name="diagnostico"', self.source)
        self.assertNotIn('name="saude"', self.source)
        bridge = (ROOT / "github_bridge.py").read_text(encoding="utf-8")
        self.assertIn('"diagnostico"', bridge)
        self.assertIn('"saude"', bridge)
        self.assertIn("reports/", bridge)
        self.assertIn("asyncio.sleep(POLL_SECONDS)", bridge)

    def test_github_bridge_has_allowlist_and_no_shell_execution(self):
        bridge = (ROOT / "github_bridge.py").read_text(encoding="utf-8")
        self.assertIn('ALLOWED_ACTIONS = {"diagnostico", "saude"}', bridge)
        self.assertNotIn("os.system(", bridge)
        self.assertNotIn("subprocess.", bridge)
        self.assertIn("GITHUB_TOKEN_FILE", bridge)


if __name__ == "__main__":
    unittest.main()
