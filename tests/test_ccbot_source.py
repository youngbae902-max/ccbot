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

    def test_diagnostic_slash_command_and_github_bridge_are_both_available(self):
        self.assertIn("import github_bridge", self.source)
        self.assertIn('@bot.tree.command(name="diagnostico"', self.source)
        self.assertIn("if not is_admin(interaction):", self.source)
        bridge = (ROOT / "github_bridge.py").read_text(encoding="utf-8")
        self.assertIn('"diagnostico"', bridge)
        self.assertIn('"saude"', bridge)
        self.assertIn("reports/", bridge)
        self.assertIn("asyncio.sleep(POLL_SECONDS)", bridge)

    def test_runtime_settings_are_separated_from_tracked_template(self):
        self.assertIn('DATA_FILE = CONFIG / "runtime_settings.json"', self.source)
        self.assertIn('LEGACY_DATA_FILE = CONFIG / "config.json"', self.source)
        self.assertIn("def load_runtime_settings():", self.source)
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("config/runtime_settings.json", ignore)
        self.assertIn("config/pix_pedidos.json", ignore)
        self.assertIn("uploads/emojis/", ignore)

    def test_json_writes_are_atomic_and_corrupt_files_are_preserved(self):
        self.assertIn("path.replace(backup)", self.source)
        self.assertIn("temporary.replace(path)", self.source)
        self.assertIn(".corrupt-", self.source)

    def test_payment_history_cleanup_requires_explicit_false_flag(self):
        self.assertIn('if settings.get("payment_admin_history_cleared") is False:', self.source)
        self.assertNotIn('settings.get("payment_admin_history_cleared", False)', self.source)

    def test_sales_deletions_adjust_counter_without_full_history_rescan(self):
        self.assertIn("ajustar_contador_vendas_por_exclusao", self.source)
        self.assertIn("len(payload.message_ids)", self.source)

    def test_receipt_files_are_attached_to_the_original_approval_message(self):
        self.assertIn("async def anexar_comprovantes_ao_pedido", self.source)
        self.assertIn("await mensagem_admin.edit(", self.source)
        self.assertIn("attachments=[*mensagem_admin.attachments, *arquivos]", self.source)
        self.assertIn("view=ConfirmarPagamentoView()", self.source)
        self.assertIn('pedido["receipt_message_id"] = str(mensagem_admin.id)', self.source)
        self.assertIn("anexo.size <= 10 * 1024 * 1024", self.source)
        self.assertNotIn('title="🧾 COMPROVANTE RECEBIDO"', self.source)

    def test_github_bridge_has_allowlist_and_no_shell_execution(self):
        bridge = (ROOT / "github_bridge.py").read_text(encoding="utf-8")
        self.assertIn('ALLOWED_ACTIONS = {"diagnostico", "saude"}', bridge)
        self.assertNotIn("os.system(", bridge)
        self.assertNotIn("subprocess.", bridge)
        self.assertIn("GITHUB_TOKEN_FILE", bridge)

    def test_startup_does_not_reconfigure_payment_channels_or_roles(self):
        start = self.source.index("@bot.event\nasync def on_ready():")
        end = self.source.index('@bot.tree.command(name="comprovante"', start)
        startup = self.source[start:end]
        self.assertNotIn("await obter_canal_confirmacao(servidor_cc)", startup)
        self.assertNotIn("await obter_canal_cancelados(servidor_cc)", startup)
        self.assertNotIn("roles = await garantir_cargos(servidor)", startup)
        self.assertIn("A inicialização não cria/move canais", startup)
        self.assertIn('if not settings.get("legacy_panels_restored", False):', startup)
        self.assertIn('if not settings.get("beats_buttons_migrated", False):', startup)
        self.assertNotIn("fetch_channel(SAMPLES_PANEL_CHANNEL_ID)", startup)
        self.assertNotIn("fetch_channel(PACK_PANEL_CHANNEL_ID)", startup)
        self.assertNotIn("bot.tree.clear_commands(guild=None)", startup)


if __name__ == "__main__":
    unittest.main()
