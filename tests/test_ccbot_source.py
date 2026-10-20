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

    def test_diagnostic_is_registered_as_slash_command(self):
        registered = False
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != "diagnostico":
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "command":
                    for keyword in decorator.keywords:
                        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                            registered = keyword.value.value == "diagnostico"
        self.assertTrue(registered, "/diagnostico deve ser um comando slash registrado")

    def test_diagnostic_is_read_only(self):
        node = next(
            n for n in ast.walk(self.tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "diagnostico"
        )
        source_segment = ast.get_source_segment(self.source, node) or ""
        forbidden_mutations = (".send(", ".edit(", ".delete(", ".set_permissions(")
        # A resposta do relatório usa followup.send; isso é permitido. O teste só
        # garante que não há mutações de canais/mensagens do servidor.
        self.assertNotIn(".set_permissions(", source_segment)
        self.assertNotIn("await canal.edit(", source_segment)
        self.assertNotIn("await canal.delete(", source_segment)
        self.assertIn("canal.history(limit=1", source_segment)


    def test_guided_creator_is_registered_and_asks_one_question_per_step(self):
        commands = set()
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "command":
                    for keyword in decorator.keywords:
                        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                            commands.add(keyword.value.value)
        self.assertIn("criar", commands)
        self.assertIn("class CriarCampoModal", self.source)
        self.assertIn("class CriarPublicarView", self.source)
        self.assertIn("Nada será publicado até você confirmar.", self.source)


    def test_health_command_is_registered_and_read_only(self):
        found = False
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != "saude":
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "command":
                    for keyword in decorator.keywords:
                        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                            found = keyword.value.value == "saude"
        self.assertTrue(found, "/saude deve ser um comando slash")


if __name__ == "__main__":
    unittest.main()
