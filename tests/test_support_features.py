import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SupportFeaturesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "support_features.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename="support_features.py")

    def test_support_module_parses(self):
        self.assertIsNotNone(self.tree)

    def test_ticket_close_fetches_owner_when_not_cached(self):
        self.assertIn("await guild.fetch_member(owner_id)", self.source)
        self.assertIn("await ch.set_permissions(owner, send_messages=False)", self.source)
        self.assertIn("Não consegui verificar o responsável pelo ticket", self.source)

    def test_ticket_close_handles_permission_failure(self):
        self.assertIn("except discord.Forbidden:", self.source)
        self.assertIn("Sem permissão para bloquear o ticket", self.source)

    def test_ticket_views_use_persistent_custom_ids(self):
        self.assertIn('custom_id="ccbot:support:close"', self.source)
        self.assertIn('custom_id="ccbot:support:open"', self.source)
        self.assertGreaterEqual(self.source.count("super().__init__(timeout=None)"), 2)


if __name__ == "__main__":
    unittest.main()
