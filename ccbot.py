import os
import json
import logging
import zipfile
from pathlib import Path
from datetime import datetime

import discord
from discord.ext import commands
from discord import app_commands

BASE = Path.home() / "ccbot-repo"
CONFIG = BASE / "config"
DATA_FILE = CONFIG / "config.json"
MODELS_FILE = CONFIG / "modelos.json"
TOKEN_FILE = Path("/sdcard/Download/CC/token.txt")
BACKUP_DIR = Path("/sdcard/Download/CC/Backups")

ADMIN_ID = 1366109646934380614
DEFAULT_NOTICE_CHANNEL = 1561698571525423115

CONFIG.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(CONFIG / "ccbot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("CCBOT")

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        save_json(path, default)
        return default

def save_json(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

settings = load_json(DATA_FILE, {
    "notice_channel": DEFAULT_NOTICE_CHANNEL
})

models = load_json(MODELS_FILE, {
    "gerador-sample": "",
    "pack-personalizado": "",
    "comprar-beats": "",
    "arrumador-projeto": ""
})

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

def is_admin(interaction: discord.Interaction):
    return interaction.user.id == ADMIN_ID

async def deny_unauthorized(interaction):
    if not is_admin(interaction):
        return True
    return False

async def notify_admin(message):
    channel_id = settings.get("notice_channel", DEFAULT_NOTICE_CHANNEL)
    channel = bot.get_channel(int(channel_id))
    if channel:
        try:
            await channel.send(
                f"⚠️ **CC BOT — Aviso**\n```text\n{message[:1800]}\n```"
            )
            return
        except Exception:
            pass
    try:
        user = await bot.fetch_user(ADMIN_ID)
        await user.send(f"⚠️ **CC BOT — Aviso**\n{message[:1800]}")
    except Exception:
        log.exception("Não foi possível enviar aviso ao administrador.")

class Modelos(app_commands.Group):
    def __init__(self):
        super().__init__(name="modelos", description="Publicar modelos nos canais")

    async def publicar(self, interaction, chave, canal):
        if await deny_unauthorized(interaction):
            return
        texto = models.get(chave, "")
        if not texto.strip():
            await interaction.response.send_message(
                f"O modelo **{chave}** ainda não tem conteúdo configurado. "
                "Precisamos adicionar o texto e o fluxo original dele.",
                ephemeral=True
            )
            return
        try:
            await canal.send(texto)
            await interaction.response.send_message(
                f"Modelo publicado em {canal.mention}.", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Não tenho permissão para enviar mensagens nesse canal.",
                ephemeral=True
            )

    @app_commands.command(name="gerador-sample", description="Publicar o modelo do Gerador de Sample")
    @app_commands.describe(canal="Canal onde publicar")
    async def gerador_sample(self, interaction: discord.Interaction, canal: discord.TextChannel):
        await self.publicar(interaction, "gerador-sample", canal)

    @app_commands.command(name="pack-personalizado", description="Publicar o modelo de Pack Personalizado")
    @app_commands.describe(canal="Canal onde publicar")
    async def pack_personalizado(self, interaction: discord.Interaction, canal: discord.TextChannel):
        await self.publicar(interaction, "pack-personalizado", canal)

    @app_commands.command(name="comprar-beats", description="Publicar o modelo de Comprar Beats")
    @app_commands.describe(canal="Canal onde publicar")
    async def comprar_beats(self, interaction: discord.Interaction, canal: discord.TextChannel):
        await self.publicar(interaction, "comprar-beats", canal)

    @app_commands.command(name="arrumador-projeto", description="Publicar o modelo do Arrumador de Projeto")
    @app_commands.describe(canal="Canal onde publicar")
    async def arrumador_projeto(self, interaction: discord.Interaction, canal: discord.TextChannel):
        await self.publicar(interaction, "arrumador-projeto", canal)

bot.tree.add_command(Modelos())

class Config(app_commands.Group):
    def __init__(self):
        super().__init__(name="config", description="Configurações do CC BOT")

    @app_commands.command(name="canal-avisos", description="Definir o canal de avisos e erros")
    async def canal_avisos(self, interaction: discord.Interaction, canal: discord.TextChannel):
        if await deny_unauthorized(interaction):
            return
        settings["notice_channel"] = canal.id
        save_json(DATA_FILE, settings)
        await interaction.response.send_message(
            f"Canal de avisos definido para {canal.mention}.", ephemeral=True
        )

    @app_commands.command(name="ver", description="Ver as configurações salvas")
    async def ver(self, interaction: discord.Interaction):
        if await deny_unauthorized(interaction):
            return
        channel_id = settings.get("notice_channel")
        await interaction.response.send_message(
            f"**Configurações do CC BOT**\n"
            f"Canal de avisos: <#{channel_id}>\n"
            f"Arquivo: `config/config.json`\n"
            f"Modelos: `config/modelos.json`",
            ephemeral=True
        )

bot.tree.add_command(Config())

class Backup(app_commands.Group):
    def __init__(self):
        super().__init__(name="backup", description="Criar backups do CC BOT")

    @app_commands.command(name="criar", description="Criar backup no Termux ou enviar por mensagem privada")
    @app_commands.choices(destino=[
        app_commands.Choice(name="Termux", value="termux"),
        app_commands.Choice(name="Discord (mensagem privada)", value="discord")
    ])
    async def criar(self, interaction: discord.Interaction, destino: app_commands.Choice[str]):
        if await deny_unauthorized(interaction):
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        arquivo = BACKUP_DIR / f"CCBOT_backup_{timestamp}.zip"

        try:
            with zipfile.ZipFile(arquivo, "w", zipfile.ZIP_DEFLATED) as z:
                for path in BASE.rglob("*"):
                    if not path.is_file():
                        continue
                    relative = path.relative_to(BASE)
                    if any(part in {".git", "__pycache__", ".venv"} for part in relative.parts):
                        continue
                    if path.name in {"token.txt", ".env"}:
                        continue
                    if path.suffix in {".pyc", ".log"}:
                        continue
                    z.write(path, relative.as_posix())

            if destino.value == "termux":
                await interaction.followup.send(
                    f"Backup criado:\n`{arquivo}`", ephemeral=True
                )
            else:
                user = await bot.fetch_user(ADMIN_ID)
                await user.send(
                    f"Backup do CC BOT — {timestamp}. O token não está incluído.",
                    file=discord.File(arquivo)
                )
                await interaction.followup.send(
                    "Backup enviado por mensagem privada.", ephemeral=True
                )
        except Exception as exc:
            log.exception("Falha ao criar backup")
            await notify_admin(f"Falha no backup: {exc}")
            await interaction.followup.send(
                "Não consegui concluir o backup. Verifique o canal de avisos.",
                ephemeral=True
            )

bot.tree.add_command(Backup())

@bot.tree.command(name="status", description="Verificar se o CC BOT está funcionando")
async def status(interaction: discord.Interaction):
    if await deny_unauthorized(interaction):
        return
    await interaction.response.send_message(
        f"CC BOT está online.\nLatência: {round(bot.latency * 1000)} ms",
        ephemeral=True
    )

@bot.tree.command(name="samples", description="Estado do módulo de geração de samples")
async def samples(interaction: discord.Interaction):
    if await deny_unauthorized(interaction):
        return
    await interaction.response.send_message(
        "O módulo está reservado na estrutura. Falta integrar a lógica real do gerador.",
        ephemeral=True
    )

@bot.tree.command(name="arrumador", description="Estado do módulo Arrumador de Projetos")
async def arrumador(interaction: discord.Interaction):
    if await deny_unauthorized(interaction):
        return
    await interaction.response.send_message(
        "O módulo está reservado na estrutura. Falta integrar a lógica real do arrumador.",
        ephemeral=True
    )

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        log.info("Comandos sincronizados: %s", len(synced))
        log.info("Conectado como %s", bot.user)
    except Exception as exc:
        log.exception("Falha ao sincronizar comandos")
        await notify_admin(f"Falha ao sincronizar comandos: {exc}")

@bot.event
async def on_app_command_error(interaction, error):
    if not is_admin(interaction):
        return
    log.error("Erro em comando slash: %s", error, exc_info=error)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                "Ocorreu um erro. O administrador foi avisado.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Ocorreu um erro. O administrador foi avisado.", ephemeral=True
            )
    except Exception:
        pass
    await notify_admin(f"Erro em comando slash: {type(error).__name__}: {error}")

if not TOKEN_FILE.is_file():
    raise SystemExit(
        f"Token não encontrado em {TOKEN_FILE}. Crie o arquivo com o token do bot."
    )

TOKEN = TOKEN_FILE.read_text(encoding="utf-8").strip()
if not TOKEN:
    raise SystemExit("O arquivo token.txt está vazio.")

bot.run(TOKEN)
