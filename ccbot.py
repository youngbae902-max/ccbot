import os
import sys
import subprocess

# CC BOT: instalação automática das dependências ausentes
try:
    import discord
    import qrcode
except ModuleNotFoundError as exc:
    if exc.name not in ("discord", "qrcode", "PIL"):
        raise
    _requirements = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
    if not os.path.isfile(_requirements):
        raise RuntimeError("requirements.txt não encontrado; não foi possível instalar as dependências.") from exc
    print(f"CC BOT: instalando dependências ausentes usando {sys.executable}...", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", _requirements])
    os.execv(sys.executable, [sys.executable, *sys.argv])
import base64
import random
import asyncio
import json
import logging
import zipfile
import io
import re
import uuid
import urllib.request
import urllib.error
import fcntl
import unicodedata
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

import discord
import qrcode
from discord.ext import commands, tasks
from discord import app_commands

import github_bridge
from support_features import configure_support
from promo_features import configure_promos

# Identidade visual global dos embeds: grafite e rodapé consistente.
# Cores, rodapés e mídias específicos de cada fluxo são preservados.
_DiscordEmbedOriginal = discord.Embed

def criar_embed_estetico(*args, **kwargs):
    """Aplica acabamento padrão sem apagar a identidade de cada painel."""
    embed = _DiscordEmbedOriginal(*args, **kwargs)
    if embed.color is None:
        embed.color = discord.Color.from_rgb(43, 45, 49)
    if not embed.footer.text:
        embed.set_footer(text="C.C ©  •  Comunidade e Produção Musical")
    return embed


BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config"
DATA_FILE = CONFIG / "runtime_settings.json"
LEGACY_DATA_FILE = CONFIG / "config.json"
MODELS_FILE = CONFIG / "modelos.json"
DJS_FILE = CONFIG / "djs.json"
TOKEN_FILE = Path("/sdcard/Download/CC/token.txt")
BACKUP_DIR = Path("/sdcard/Download/CC/Backups")
BEAT_PREVIEWS_DIR = Path("/sdcard/Download/CC/BeatPreviews")
BEATS_FILE = CONFIG / "beats.json"

ADMIN_ID = 1366109646934380614
ORDERS_CHANNEL_ID = 1561513284191125625
DEFAULT_NOTICE_CHANNEL = 1561698571525423115
WELCOME_CHANNEL_ID = 1561513269754068992

# GIFs públicos diretos para variar a imagem do aviso de inicialização.
STARTUP_GIFS = [
    "https://cdn-anibox.pages.dev/dance/4.gif",
    "https://cdn-anibox.pages.dev/pat/10.gif",
    "https://cdn-anibox.pages.dev/hug/1.gif",
]

# REGRA PERMANENTE DE MANUTENÇÃO:
# Ao alterar painéis do Discord, edite a mensagem já publicada sempre que possível.
# Não publique outro embed/painel nem duplique mensagens sem pedido explícito do administrador
# ou necessidade técnica real. O painel de Samples existente está identificado abaixo.
SAMPLES_PANEL_CHANNEL_ID = 1561513284811624480
SAMPLES_PANEL_MESSAGE_ID = 1561742192656261183
PACK_PANEL_CHANNEL_ID = 1561513286967500841
PACK_PANEL_MESSAGE_ID = 1561787024829321287

CONFIG.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
BEAT_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)

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
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        save_json(path, default)
        return default
    except OSError:
        log.exception("Não foi possível ler o arquivo %s.", path)
        raise

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        backup = path.with_name(
            f"{path.name}.corrupt-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        try:
            path.replace(backup)
        except OSError as backup_error:
            log.exception(
                "JSON inválido em %s e não foi possível preservar o original.",
                path
            )
            raise RuntimeError(
                f"Não foi possível preservar o JSON inválido: {path}"
            ) from backup_error
        log.error(
            "JSON inválido em %s; original preservado em %s.",
            path,
            backup,
            exc_info=(type(exc), exc, exc.__traceback__)
        )
        save_json(path, default)
        return default

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    try:
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            log.warning("Não foi possível limpar o arquivo temporário %s.", temporary)

def load_runtime_settings():
    """Migra configurações antigas para um arquivo local ignorado pelo Git."""
    legacy_defaults = {"notice_channel": DEFAULT_NOTICE_CHANNEL}
    legacy = load_json(LEGACY_DATA_FILE, legacy_defaults)
    runtime = load_json(DATA_FILE, {})
    if not isinstance(legacy, dict):
        raise RuntimeError(
            f"{LEGACY_DATA_FILE} precisa conter um objeto JSON; dados preservados."
        )
    if not isinstance(runtime, dict):
        raise RuntimeError(
            f"{DATA_FILE} precisa conter um objeto JSON; dados preservados."
        )

    merged = {**legacy, **runtime}
    save_json(DATA_FILE, merged)

    # O arquivo legado é versionado; mantém nele somente o modelo do repositório
    # para que o bot não deixe o Git sujo a cada venda ou alteração de configuração.
    save_json(LEGACY_DATA_FILE, legacy_defaults)
    return merged

settings = load_runtime_settings()

models = load_json(MODELS_FILE, {
    "gerador-sample": "",
    "pack-personalizado": "",
    "comprar-beats": "",
    "arrumador-projeto": ""
})

# Registro persistente das prévias publicadas para restaurar os botões após reinícios.
beat_records = load_json(BEATS_FILE, {})
if not isinstance(beat_records, dict):
    beat_records = {}
    save_json(BEATS_FILE, beat_records)

# A lista só contém DJs cadastrados pelo administrador no comando /painel.
mcs = load_json(DJS_FILE, [])
if not isinstance(mcs, list):
    mcs = []
    save_json(DJS_FILE, mcs)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
class CCBot(commands.Bot):
    async def setup_hook(self):
        """Registra componentes persistentes antes de o bot conectar ao Discord."""
        await super().setup_hook()
        views_persistentes = [
            ("Gerador de Samples", GeradorSamplesView),
            ("Pack Personalizado", PackPersonalizadoView),
            ("Arrumador de Projetos", ArrumadorPainelView),
            ("Acapellas", AcapellasActivityView),
            ("Trampar Conosco", TramparConoscoView),
            ("Comprar Beats", lambda: VendaBeatView("", "", "")),
            ("Ver icons", IconVerIconsView),
            ("Central de Ícones", PainelIconesView),
            ("Packs grátis", lambda: FreeProjectsAccessView("packs")),
            ("Projetos grátis", lambda: FreeProjectsAccessView("projetos")),
            ("Painel de suporte", SupportPanelView),
            ("Encerramento de ticket", TicketCloseView),
        ]
        for nome_view, criar_view in views_persistentes:
            try:
                self.add_view(criar_view())
                log.info("View persistente registrada antes da conexão: %s", nome_view)
            except Exception:
                log.exception("Falha ao registrar view persistente: %s", nome_view)
        try:
            self.add_view(ConfirmarPagamentoView())
            log.info("View persistente de confirmação manual de pagamentos registrada.")
        except Exception:
            log.exception("Falha ao registrar a view de confirmação manual de pagamentos.")

bot = CCBot(command_prefix="!", intents=intents)

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

async def enviar_pedido_admin(mensagem, embed=None):
    """Envia pedidos somente ao canal central de pedidos, nunca ao PV."""
    try:
        canal = bot.get_channel(ORDERS_CHANNEL_ID)
        if canal is None:
            canal = await bot.fetch_channel(ORDERS_CHANNEL_ID)
        await canal.send(content=mensagem[:1900] if mensagem else None, embed=embed)
    except Exception:
        log.exception("Não foi possível enviar pedido ao canal central %s.", ORDERS_CHANNEL_ID)
        raise

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

BEAT_AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".ogg", ".oga", ".opus", ".m4a", ".aac",
    ".flac", ".aiff", ".aif", ".wma", ".mid", ".midi", ".amr", ".3gp"
}

class ConfirmarPublicacaoBeatView(discord.ui.View):
    def __init__(self, canal, nome, descricao, estilo, preco, preview_path):
        super().__init__(timeout=180)
        self.canal = canal
        self.nome = nome
        self.descricao = descricao
        self.estilo = estilo
        self.preco = preco
        self.preview_path = Path(preview_path)

    @discord.ui.button(label="Confirmar", emoji="✅", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Apenas o administrador pode confirmar a publicação.",
                ephemeral=True
            )
            return

        # Responde primeiro à interação para evitar que ela expire durante a publicação.
        await interaction.response.defer(ephemeral=True, thinking=True)

        embed = criar_embed_estetico(
            title=f"🎧 {self.nome}",
            description=self.descricao or "Sem descrição.",
            color=discord.Color.green()
        )
        embed.add_field(name="Estilo", value=self.estilo or "Não informado", inline=True)
        embed.add_field(name="Valor", value=self.preco, inline=True)
        embed.add_field(
            name="Prévia",
            value="Clique em **Ouvir prévia** para escutar o áudio.",
            inline=False
        )
        embed.set_footer(text="CC BOT • Venda de Beats")

        view = VendaBeatView(
            nome=self.nome,
            preco=self.preco,
            preview_path=self.preview_path
        )
        try:
            mensagem_publicada = await self.canal.send(embed=embed, view=view)
            beat_records[str(mensagem_publicada.id)] = {"preview_path": str(self.preview_path)}
            save_json(BEATS_FILE, beat_records)
            log.info(
                "Beat publicado com sucesso: nome=%r canal=%s mensagem=%s",
                self.nome, self.canal.id, mensagem_publicada.id
            )
            await interaction.edit_original_response(
                content=f"✅ Beat **{self.nome}** publicado em {self.canal.mention}.",
                view=None,
                attachments=[]
            )
        except Exception as exc:
            log.exception("Falha ao publicar beat confirmado")
            await interaction.edit_original_response(
                content=f"❌ Não consegui publicar o beat ({type(exc).__name__}). Verifique as permissões do bot no canal.",
                view=None
            )

    @discord.ui.button(label="Cancelar", emoji="✖️", style=discord.ButtonStyle.danger)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode cancelar esta publicação.", ephemeral=True)
            return
        try:
            self.preview_path.unlink(missing_ok=True)
        except OSError:
            log.exception("Não foi possível apagar a prévia de um beat cancelado")
        await interaction.response.edit_message(
            content="❌ Publicação cancelada. O embed do beat não foi enviado.",
            view=None
        )

class BeatInfoModal(discord.ui.Modal, title="Cadastrar beat para venda"):
    nome = discord.ui.TextInput(label="Nome do beat", placeholder="Ex.: Noite de Baile", max_length=100)
    descricao = discord.ui.TextInput(label="Descrição", placeholder="Descreva o beat", style=discord.TextStyle.paragraph, max_length=1000)
    estilo = discord.ui.TextInput(label="Estilo do beat", placeholder="Ex.: Funk mandelão", max_length=100)
    valor = discord.ui.TextInput(label="Valor (R$)", placeholder="Ex.: 25,00", max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode cadastrar beats.", ephemeral=True)
            return

        try:
            valor_texto = str(self.valor.value).strip().replace("R$", "").replace(" ", "")
            valor_texto = valor_texto.replace(".", "").replace(",", ".")
            valor_numero = float(valor_texto)
            if valor_numero < 0:
                raise ValueError
        except (ValueError, TypeError):
            await interaction.response.send_message(
                "Valor inválido. Informe um preço como 25,00 ou 25.00.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        canal = interaction.channel
        if interaction.guild is None or not isinstance(canal, (discord.TextChannel, discord.Thread)):
            await interaction.edit_original_response(
                content="❌ Use /venda-de-beats em um canal de texto do servidor.",
                view=None
            )
            return

        instrucoes = criar_embed_estetico(
            title="🎧 Envio da prévia do beat",
            description=(
                "Envie **o arquivo de áudio neste canal** para continuar o cadastro.\n\n"
                "・ Aceito: MP3, WAV, OGG, M4A e outros formatos de áudio.\n"
                "・ Você tem **3 minutos** para enviar o arquivo.\n"
                "・ Assim que o áudio for recebido, o beat será publicado automaticamente."
            ),
            color=discord.Color.blurple()
        )
        instrucoes.set_footer(text="CC BOT • Venda de Beats")

        await interaction.edit_original_response(
            content=None,
            embed=instrucoes,
            view=None
        )

        def mensagem_valida(message: discord.Message):
            return (
                message.author.id == interaction.user.id
                and message.guild is not None
                and message.channel.id == canal.id
                and bool(message.attachments)
            )

        prazo_final = asyncio.get_running_loop().time() + 180
        audio = None
        mensagem = None

        while audio is None:
            restante = prazo_final - asyncio.get_running_loop().time()
            if restante <= 0:
                await interaction.edit_original_response(
                    content="⏰ **Tempo esgotado.** Execute /venda-de-beats novamente para cadastrar o beat.",
                    view=None
                )
                return

            try:
                mensagem = await bot.wait_for(
                    "message", check=mensagem_valida, timeout=restante
                )
            except asyncio.TimeoutError:
                await interaction.edit_original_response(
                    content="⏰ **Tempo esgotado.** Execute /venda-de-beats novamente para cadastrar o beat.",
                    view=None
                )
                return

            try:
                await interaction.edit_original_response(
                    content=f"📥 Recebi **{mensagem.attachments[0].filename}** no canal. Verificando o formato...",
                    embed=None
                )
            except discord.HTTPException:
                pass

            for arquivo in mensagem.attachments:
                nome_arquivo = Path(arquivo.filename).name.lower()
                tipo = (arquivo.content_type or "").lower()
                if tipo.startswith("audio/") or Path(nome_arquivo).suffix in BEAT_AUDIO_EXTENSIONS:
                    audio = arquivo
                    break

            if audio is None:
                try:
                    await interaction.followup.send(
                        "⚠️ O arquivo enviado não parece ser um áudio compatível. Envie MP3, WAV, OGG, M4A ou outro formato de áudio.",
                        ephemeral=True
                    )
                except discord.HTTPException:
                    pass

        try:
            await interaction.edit_original_response(
                content=f"✅ Áudio reconhecido: `{audio.filename}`. Baixando o arquivo...",
                embed=None
            )
            safe_name = Path(audio.filename).name.replace("/", "_").replace("\\", "_")
            preview_path = BEAT_PREVIEWS_DIR / f"{uuid.uuid4().hex}_{safe_name}"

            # Baixa os bytes diretamente e grava em disco fora do loop do bot.
            # Isso evita travamentos do Attachment.save em anexos de mensagem de voz.
            dados_audio = await asyncio.wait_for(audio.read(use_cached=True), timeout=30)
            if not dados_audio:
                raise ValueError("O Discord retornou um arquivo de áudio vazio.")

            await interaction.edit_original_response(
                content=f"📦 Áudio baixado ({len(dados_audio) // 1024} KB). Salvando...",
                embed=None
            )
            await asyncio.wait_for(
                asyncio.to_thread(preview_path.write_bytes, dados_audio),
                timeout=15
            )

            try:
                await mensagem.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass
        except asyncio.TimeoutError:
            log.error("Timeout ao baixar/salvar a prévia do beat: %s", audio.filename)
            await interaction.edit_original_response(
                content="⏳ O download do áudio excedeu o limite de tempo. Tente enviar o arquivo como anexo normal MP3, WAV ou OGG, não como mensagem de voz.",
                view=None
            )
            return
        except Exception as exc:
            log.exception("Falha ao baixar/salvar a prévia do beat")
            await interaction.edit_original_response(
                content=f"❌ Não consegui preparar a prévia ({type(exc).__name__}: {str(exc)[:180]}). Tente enviar o arquivo como anexo normal MP3, WAV ou OGG.",
                view=None            )
            return

        preco = f"R$ {valor_numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        nome_beat = self.nome.value.strip()
        descricao_beat = self.descricao.value.strip()
        estilo_beat = self.estilo.value.strip()

        embed = criar_embed_estetico(
            title=f"🎧 {nome_beat}",
            description=descricao_beat or "Sem descrição.",
            color=discord.Color.green()
        )
        embed.add_field(name="Estilo", value=estilo_beat or "Não informado", inline=True)
        embed.add_field(name="Valor", value=preco, inline=True)
        embed.add_field(
            name="Prévia",
            value="Clique em **Ouvir prévia** para escutar o áudio.",
            inline=False
        )
        embed.set_footer(text="CC BOT • Venda de Beats")

        try:
            view = VendaBeatView(
                nome=nome_beat,
                preco=preco,
                preview_path=preview_path
            )
            mensagem_publicada = await canal.send(embed=embed, view=view)
            beat_records[str(mensagem_publicada.id)] = {"preview_path": str(preview_path)}
            save_json(BEATS_FILE, beat_records)
            await interaction.edit_original_response(
                content=f"✅ Beat **{nome_beat}** publicado em {canal.mention}.",
                view=None,
                attachments=[]
            )
            log.info("Beat publicado automaticamente: nome=%r canal=%s", nome_beat, canal.id)
        except Exception as exc:
            log.exception("Falha ao publicar beat automaticamente")
            await interaction.edit_original_response(
                content=f"❌ O áudio foi salvo, mas não consegui publicar o beat ({type(exc).__name__}). Verifique as permissões do bot no canal.",
                view=None
            )

class OuvirPreviaBeatButton(discord.ui.Button):
    def __init__(self, preview_path):
        super().__init__(label="Ouvir prévia", emoji="🎧", style=discord.ButtonStyle.primary, custom_id="ccbot:beat:preview")
        self.preview_path = str(preview_path or "")

    async def callback(self, interaction: discord.Interaction):
        # PRIMEIRA operação: reconhecer o clique antes de acessar disco ou enviar arquivo.
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except discord.HTTPException:
            log.exception("Não foi possível confirmar a interação de prévia.")
            return

        try:
            caminho = self.preview_path
            if not caminho or not Path(caminho).is_file():
                if interaction.message:
                    registro = beat_records.get(str(interaction.message.id), {})
                    caminho = str(registro.get("preview_path", "") or "")

            if not caminho or not Path(caminho).is_file():
                await interaction.followup.send(
                    "A prévia não está disponível no armazenamento do bot. O administrador precisa cadastrar o beat novamente.",
                    ephemeral=True
                )
                return

            preview_path = Path(caminho)
            await interaction.followup.send(
                content="🎧 **Prévia do beat** — toque no áudio anexado para ouvir.",
                file=discord.File(preview_path, filename=preview_path.name),
                ephemeral=True
            )
        except Exception as exc:
            log.exception("Falha ao enviar a prévia do beat: %s", self.preview_path)
            try:
                await interaction.followup.send(
                    f"❌ Não consegui enviar o áudio da prévia ({type(exc).__name__}). O arquivo pode estar grande demais ou indisponível.",
                    ephemeral=True
                )
            except Exception:
                log.exception("Falha ao informar erro da prévia do beat")

class ComprarBeatButton(discord.ui.Button):
    def __init__(self, nome: str, preco: str):
        super().__init__(label="Solicitar beat", emoji="🛒", style=discord.ButtonStyle.primary, custom_id="ccbot:beat:buy")
        self.nome = nome
        self.preco = preco

    async def callback(self, interaction: discord.Interaction):
        nome = self.nome or "Beat"
        preco_texto = self.preco or "Consultar"
        if interaction.message and interaction.message.embeds:
            embed_beat = interaction.message.embeds[0]
            if not self.nome:
                nome = (embed_beat.title or "Beat").replace("🎧 ", "", 1).strip()
            if not self.preco:
                preco_texto = next(
                    (field.value for field in embed_beat.fields if field.name.casefold() == "valor"),
                    "Consultar"
                )

        numero = re.sub(r"[^0-9,.]", "", str(preco_texto))
        try:
            if "," in numero:
                numero = numero.replace(".", "").replace(",", ".")
            valor = float(numero)
        except (TypeError, ValueError):
            valor = 0
        if valor <= 0:
            await interaction.response.send_message(
                "Este beat ainda não tem um preço válido no anúncio. O administrador precisa cadastrar o valor antes de abrir o pagamento.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            ticket, txid = await criar_pedido_pagamento(
                interaction, nome, valor,
                f"Beat: {nome}\nPreço anunciado: {formatar_reais(valor)}",
                tipo="beat"
            )
            await interaction.edit_original_response(
                content=(
                    f"Pagamento pendente para **{nome}** ({formatar_reais(valor)}). "
                    f"Acesse o tópico privado {ticket.mention} para pagar. Identificador: {txid}. "
                    "O beat só será liberado após a conferência manual do administrador."
                ),
                view=None
            )
        except Exception as exc:
            log.exception("Falha ao abrir checkout Pix para o beat %s", nome)
            await interaction.edit_original_response(
                content=f"Não consegui abrir o pagamento: {str(exc)[:400]}",
                view=None
            )

class VendaBeatView(discord.ui.View):
    def __init__(self, nome: str, preco: str, preview_path):
        super().__init__(timeout=None)
        self.add_item(OuvirPreviaBeatButton(preview_path))
        self.add_item(ComprarBeatButton(nome, preco))

async def venda_de_beats(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador pode cadastrar beats para venda.",
            ephemeral=True
        )
        return
    await interaction.response.send_modal(BeatInfoModal())

class PackBasicoModal(discord.ui.Modal, title="Encomendar Pack Personalizado"):
    nome = discord.ui.TextInput(
        label="Nome do pack",
        placeholder="Ex.: Pack Mandelão do DJ X",
        max_length=100
    )
    estilo = discord.ui.TextInput(
        label="Estilo musical",
        placeholder="Qual funk/sonoridade? Ex.: mandelão, automotivo...",
        max_length=100
    )
    detalhes = discord.ui.TextInput(
        label="O que você quer no pack?",
        placeholder="Descreva a ideia, referências, sons desejados e o resultado que espera.",
        style=discord.TextStyle.paragraph,
        max_length=900
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_message(
                view=PackAdicionaisView(interaction.user.id, str(self.nome), str(self.estilo), str(self.detalhes)),
                ephemeral=True
            )
        except Exception as exc:
            log.exception("Falha ao abrir adicionais do Pack Personalizado")
            if not interaction.response.is_done():
                await interaction.response.send_message("Não consegui abrir os adicionais. Tente novamente.", ephemeral=True)

class PackPremiumModal(discord.ui.Modal, title="Encomendar Pack Premium"):
    nome = discord.ui.TextInput(
        label="Nome do pack",
        placeholder="Dê um nome para identificar seu pack",
        max_length=100
    )
    estilo = discord.ui.TextInput(
        label="Estilo musical",
        placeholder="Ex.: mandelão, rave, automotivo, montagem...",
        max_length=100
    )
    itens = discord.ui.TextInput(
        label="O que deve vir no pack?",
        placeholder="Liste kicks, samples, acapellas, MCs, vozes antes da virada etc.",
        style=discord.TextStyle.paragraph,
        max_length=900
    )
    quantidades = discord.ui.TextInput(
        label="Quantidades e organização",
        placeholder="Quantos de cada item? Quer pastas separadas? (opcional)",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await enviar_pedido_pack(
            interaction, "Pack Premium", 100,
            {"Nome": str(self.nome), "Estilo": str(self.estilo), "Itens": str(self.itens),
             "Quantidades/organização": str(self.quantidades) or "A combinar"},
            []
        )

class PackAdicionais(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Adicionais opcionais",
            min_values=0,
            max_values=3,
            options=[
                discord.SelectOption(
                    label="Organização das pastas (+ R$ 10)",
                    description="Deixa os arquivos separados e fáceis de encontrar.",
                    value="Organização das pastas (+ R$ 10)"
                ),
                discord.SelectOption(
                    label="Samples personalizadas (+ R$ 10)",
                    description="Samples pensadas para a ideia do seu pack.",
                    value="Samples personalizadas (+ R$ 10)"
                ),
                discord.SelectOption(
                    label="Samples de outros packs (+ R$ 20)",
                    description="Adiciona samples que vêm de outros packs.",
                    value="Samples de outros packs (+ R$ 20)"
                )
            ]
        )

    async def callback(self, interaction: discord.Interaction):
        # O menu só atualiza a seleção; confirma a interação imediatamente.
        await interaction.response.defer()

async def enviar_pedido_pack(interaction, plano, valor, detalhes, adicionais):
    try:
        # O pedido é enviado ao PV do administrador e ao canal de avisos.
        embed = criar_embed_estetico(
            title="📦 Novo pedido • Pack Personalizado",
            description=f"**Plano:** {plano}\n**Valor estimado:** R$ {valor},00",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Cliente", value=f"{interaction.user} (ID {interaction.user.id})", inline=False)
        embed.add_field(name="Servidor", value=interaction.guild.name if interaction.guild else "Não informado", inline=False)
        embed.add_field(name="Adicionais", value=", ".join(adicionais) if adicionais else "Nenhum", inline=False)
        for titulo, texto in detalhes.items():
            embed.add_field(name=titulo[:256], value=(str(texto).strip() or "Não informado")[:1024], inline=False)
        embed.set_footer(text="CC BOT • Pack Personalizado")

        await enviar_pedido_admin(None, embed=embed)
        # Substitui a tela privada atual pela confirmação, em vez de criar
        # outra resposta efêmera separada.
        await interaction.edit_original_response(
            content=f"✅ Pedido de **{plano}** enviado! Valor estimado: **R$ {valor},00**. O administrador entrará em contato.",
            embed=None,
            view=None
        )
    except discord.Forbidden:
        log.exception("O administrador bloqueou mensagens privadas do bot.")
        try:
            await interaction.followup.send(
                "Não consegui enviar o pedido por PV. Ative as mensagens diretas do servidor para receber pedidos do bot e tente novamente.",
                ephemeral=True
            )
        except Exception:
            pass
    except Exception as exc:
        log.exception("Falha ao enviar pedido de pack por PV")
        try:
            await interaction.followup.send(
                f"Não consegui enviar o pedido por PV ({type(exc).__name__}). Avise o administrador.",
                ephemeral=True
            )
        except Exception:
            pass

class PackAdicionaisView(discord.ui.LayoutView):
    def __init__(self, owner_id, nome, estilo, detalhes):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.nome = nome
        self.estilo = estilo
        self.detalhes = detalhes
        self.seletor = PackAdicionais()
        enviar = discord.ui.Button(label="Enviar pedido", emoji="📨", style=discord.ButtonStyle.success, custom_id="ccbot:pack:enviar")
        enviar.callback = self.enviar
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(content=(
                "## 🏷️ PACK PERSONALIZADO — R$ 50,00\n"
                "Ideal para quem quer um pack montado a partir de uma ideia específica.\n"
                "O valor inclui cerca de 3 horas de trabalho (R$ 30) + conhecimento aplicado (R$ 20).\n\n"
                "**O que fazer agora:** marque os adicionais que quiser e toque em **Enviar pedido**. "
                "Se não quiser extras, deixe tudo desmarcado.\n\n"
                "**Adicionais (opcionais):**\n"
                "• **Organização das pastas (+ R$ 10):** arquivos separados e organizados.\n"
                "• **Samples personalizadas (+ R$ 10):** samples feitas para combinar com sua ideia.\n"
                "• **Samples de outros packs (+ R$ 20):** incluir samples de outros packs.\n\n"
                "O total é calculado automaticamente antes do pedido ser enviado."
            )),
            discord.ui.ActionRow(self.seletor),
            discord.ui.ActionRow(enviar)
        ))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Este pedido pertence a outra pessoa.", ephemeral=True)
            return False
        return True

    async def enviar(self, interaction):
        extras = list(self.seletor.values)
        valor = 50
        for item in extras:
            if "Organização" in item or "personalizadas" in item:
                valor += 10
            elif "outros packs" in item:
                valor += 20
        await interaction.response.defer(ephemeral=True, thinking=True)
        await enviar_pedido_pack(
            interaction, "Pack Personalizado", valor,
            {"Nome do pack": self.nome, "Estilo musical": self.estilo, "Detalhes": self.detalhes},
            extras
        )
        try:
            await interaction.edit_original_response(view=None)
        except discord.HTTPException:
            pass

PACK_QUESTIONS = {
    "Pack Personalizado": [
        ("Nome do pack", "Qual nome você quer dar ao pack?", "Pode ser o nome do DJ ou uma ideia que identifique o projeto."),
        ("Estilo musical", "Qual estilo musical você quer?", "Ex.: mandelão, automotivo, rave, montagem ou mistura."),
        ("Ideia e detalhes", "Como você imagina esse pack?", "Conte quais sons, referências e elementos não podem faltar.")
    ],
    "Pack Premium": [
        ("Nome do pack", "Qual nome você quer dar ao pack?", "Pode ser o nome do DJ ou uma ideia que identifique o projeto."),
        ("Estilo musical", "Qual estilo musical você quer?", "Ex.: mandelão, automotivo, rave, montagem ou mistura."),
        ("Itens desejados", "O que você quer receber no pack Premium?", "Cite kicks, samples, acapellas, MCs, vozes antes da virada e outros elementos."),
        ("Quantidades", "Tem alguma quantidade em mente?", "Diga aproximadamente quantos itens quer de cada tipo, ou escreva a combinar."),
        ("Organização", "Como prefere a organização?", "Ex.: pastas por tipo, nome do DJ, BPM ou outra preferência.")
    ]
}
PACK_EXTRAS = [
    ("Organização das pastas", 10, "Quer que eu organize os arquivos em pastas?"),
    ("Samples personalizadas", 10, "Quer samples personalizadas para combinar com sua ideia?"),
    ("Samples de outros packs", 20, "Quer incluir samples de outros packs?")
]

async def iniciar_fluxo_pack(interaction: discord.Interaction, plano: str):
    """Abre o fluxo dentro da própria resposta efêmera do painel, sem PV e sem canal novo."""
    if interaction.guild is None:
        await interaction.response.send_message("Use este painel dentro do servidor.", ephemeral=True)
        return
    fluxo = PackPerguntasView(interaction.user.id, plano, interaction.guild.id, interaction)
    await interaction.response.send_message(view=fluxo, ephemeral=True)

class PackRespostaModal(discord.ui.Modal):
    def __init__(self, parent, titulo, pergunta, dica):
        super().__init__(title=titulo[:45], timeout=300)
        self.parent_view = parent
        self.resposta = discord.ui.TextInput(
            label=pergunta[:45],
            placeholder=dica[:100],
            style=discord.TextStyle.paragraph if len(dica) > 55 else discord.TextStyle.short,
            max_length=900,
            required=True
        )
        self.add_item(self.resposta)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.owner_id:
            await interaction.response.send_message("Este pedido pertence a outra pessoa.", ephemeral=True)
            return
        try:
            texto = str(self.resposta.value).strip()
            if not texto:
                await interaction.response.send_message("Escreva uma resposta antes de continuar.", ephemeral=True)
                return
            self.parent_view.salvar_resposta(texto)
            # O envio do modal não deve editar a resposta de uma interação antiga.
            # Criamos a próxima etapa como resposta efêmera; os botões seguintes
            # atualizam diretamente a mensagem em que foram clicados.
            await self.parent_view.avancar_estado()
            self.parent_view.rebuild()
            await interaction.response.send_message(view=self.parent_view, ephemeral=True)
        except Exception:
            log.exception("Falha ao processar resposta do modal Pack Personalizado")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Não consegui registrar essa resposta. Tente novamente.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "Não consegui registrar essa resposta. Tente novamente.",
                        ephemeral=True
                    )
            except Exception:
                log.exception("Também falhou o aviso de erro do modal Pack Personalizado")

class PackPerguntasView(discord.ui.LayoutView):
    def __init__(self, owner_id: int, plano: str, guild_id: int, painel_interaction):
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.painel_interaction = painel_interaction
        self.plano = plano
        self.guild_id = guild_id
        self.respostas = {}
        self.indice = 0
        self.extras_indice = 0
        self.extras_escolhidos = []
        self.fase = "perguntas"
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Este pedido pertence a outra pessoa.", ephemeral=True)
            return False
        return True

    def salvar_resposta(self, texto):
        if self.fase == "perguntas":
            titulo = PACK_QUESTIONS[self.plano][self.indice][0]
            self.respostas[titulo] = texto

    def rebuild(self):
        self.clear_items()
        if self.fase == "perguntas":
            perguntas = PACK_QUESTIONS[self.plano]
            titulo, pergunta, dica = perguntas[self.indice]
            total = len(perguntas) + (len(PACK_EXTRAS) if self.plano == "Pack Personalizado" else 0)
            numero = self.indice + 1
            conteudo = (
                f"## PERGUNTA {numero}/{total}\n"
                f"### {pergunta}\n{dica}\n\n"
                "Toque em **Responder**. A próxima pergunta aparecerá aqui nesta mesma aba."
            )
            botao = discord.ui.Button(label="Responder", emoji="✍️", style=discord.ButtonStyle.primary, custom_id="ccbot:pack:responder")
            botao.callback = self.abrir_modal
            itens = [botao]
        elif self.fase == "extras":
            nome, preco, pergunta = PACK_EXTRAS[self.extras_indice]
            conteudo = (
                f"## 📦 PACK PERSONALIZADO — ADICIONAIS\n"
                f"**Adicional {self.extras_indice + 1} de {len(PACK_EXTRAS)}**\n\n"
                f"### {pergunta}\n"
                f"Valor adicional: **R$ {preco},00**\n\nEscolha uma opção abaixo."
            )
            sim = discord.ui.Button(label="Sim", emoji="✅", style=discord.ButtonStyle.success, custom_id="ccbot:pack:extra:sim")
            nao = discord.ui.Button(label="Não", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="ccbot:pack:extra:nao")
            sim.callback = self.responder_extra_sim
            nao.callback = self.responder_extra_nao
            itens = [sim, nao]
        else:
            total = 50 + sum(preco for _, preco in self.extras_escolhidos) if self.plano == "Pack Personalizado" else 100
            extras = "\n".join(f"• {n} — R$ {p},00" for n, p in self.extras_escolhidos) or "• Nenhum adicional"
            resumo = "\n".join(f"**{k}:** {v}" for k, v in self.respostas.items())
            conteudo = (
                "## 🧾 RECIBO DO PEDIDO\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"**Plano:** {self.plano}\n\n"
                "### 💰 VALOR ESTIMADO\n"
                f"## R$ {total},00\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "### Adicionais\n"
                f"{extras}\n\n"
                "### Detalhes do pedido\n"
                f"{resumo}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Confira os dados acima. Se estiver tudo certo, toque em **Enviar pedido**."
            )
            enviar = discord.ui.Button(label="Enviar pedido", emoji="📨", style=discord.ButtonStyle.success, custom_id="ccbot:pack:finalizar")
            voltar = discord.ui.Button(label="Voltar", style=discord.ButtonStyle.secondary, custom_id="ccbot:pack:voltar")
            enviar.callback = self.finalizar
            voltar.callback = self.voltar
            itens = [voltar, enviar]
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(content=conteudo[:3900]),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
            discord.ui.ActionRow(*itens)
        ))

    async def abrir_modal(self, interaction):
        try:
            titulo, pergunta, dica = PACK_QUESTIONS[self.plano][self.indice]
            await interaction.response.send_modal(PackRespostaModal(self, titulo, pergunta, dica))
        except Exception:
            log.exception("Falha ao abrir modal do Pack Personalizado")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Não consegui abrir o campo de resposta. Clique novamente em Responder.",
                        ephemeral=True
                    )
            except Exception:
                log.exception("Falha ao avisar sobre erro ao abrir modal do Pack")

    async def avancar_estado(self):
        if self.fase == "perguntas":
            self.indice += 1
            if self.indice >= len(PACK_QUESTIONS[self.plano]):
                if self.plano == "Pack Personalizado":
                    self.fase = "extras"
                else:
                    self.fase = "revisao"
        elif self.fase == "extras":
            self.extras_indice += 1
            if self.extras_indice >= len(PACK_EXTRAS):
                self.fase = "revisao"

    async def mostrar_proxima(self):
        await self.avancar_estado()
        self.rebuild()
        await self.painel_interaction.edit_original_response(view=self)

    async def responder_extra_sim(self, interaction):
        nome, preco, _ = PACK_EXTRAS[self.extras_indice]
        if not any(n == nome for n, _ in self.extras_escolhidos):
            self.extras_escolhidos.append((nome, preco))
        await self.avancar_estado()
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def responder_extra_nao(self, interaction):
        await self.avancar_estado()
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def voltar(self, interaction):
        if self.fase == "revisao":
            if self.plano == "Pack Personalizado":
                self.fase = "extras"
                self.extras_indice = len(PACK_EXTRAS) - 1
            else:
                self.fase = "perguntas"
                self.indice = len(PACK_QUESTIONS[self.plano]) - 1
        elif self.fase == "extras":
            if self.extras_indice > 0:
                self.extras_indice -= 1
            else:
                self.fase = "perguntas"
                self.indice = len(PACK_QUESTIONS[self.plano]) - 1
        elif self.fase == "perguntas" and self.indice > 0:
            self.indice -= 1
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def finalizar(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        total = 50 + sum(preco for _, preco in self.extras_escolhidos) if self.plano == "Pack Personalizado" else 100
        detalhes = (
            "Adicionais: " + (", ".join(f"{n} (+ {formatar_reais(p)})" for n, p in self.extras_escolhidos) or "Nenhum")
            + "\n" + "\n".join(f"{titulo}: {texto}" for titulo, texto in self.respostas.items())
        )
        try:
            ticket, txid = await criar_pedido_pagamento(
                interaction, self.plano, total, detalhes, tipo="pack"
            )
            self.clear_items()
            self.add_item(discord.ui.Container(discord.ui.TextDisplay(
                content=(
                    f"## PAGAMENTO PENDENTE\n"
                    f"Seu pedido de **{self.plano}** foi criado.\n"
                    f"Valor: {formatar_reais(total)}\n"
                    f"Identificador: {txid}\n"
                    f"Entre no canal privado {ticket.mention} para ver o QR Code e o código Pix copia e cola. "
                    "Aguarde o administrador confirmar o pagamento."
                )
            )))
            await interaction.edit_original_response(view=self)
        except Exception as exc:
            log.exception("Falha ao criar checkout manual do Pack Personalizado")
            await interaction.edit_original_response(
                content=f"Não consegui abrir o pagamento: {str(exc)[:500]}",
                view=None
            )

class PackPersonalizadoView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        basico = discord.ui.Button(
            label="Montar meu pack",
            emoji="🏷️",
            style=discord.ButtonStyle.primary,
            custom_id="ccbot:pack:v2:basico"
        )
        basico.callback = self.abrir_basico
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(content=(
                "## 📦 PACK PERSONALIZADO\n"
                "### 🏷️ Pack sob medida — R$ 50,00\n\n"
                "Um pack criado a partir da sua ideia, estilo e identidade sonora.\n"
                "Responda às perguntas e escolha somente os adicionais que quiser.\n\n"
                "**O que está incluído**\n"
                "• Nome, estilo musical e descrição do projeto\n"
                "• Cerca de 3 horas de trabalho + conhecimento aplicado\n\n"
                "**Adicionais opcionais**\n"
                "🗂️ Organização das pastas · + R$ 10\n"
                "🎛️ Samples personalizadas · + R$ 10\n"
                "🎚️ Samples de outros packs · + R$ 20\n\n"
                "O total é calculado conforme os extras selecionados. Toque abaixo para começar."
            )),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=(
                "### ✦ ADICIONAIS\n"
                "🗂️ **Organização** · + R$ 10\n"
                "🎛️ **Samples personalizadas** · + R$ 10\n"
                "🎚️ **Samples de outros packs** · + R$ 20"
            )),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=(
                "Informe o nome, o estilo e a ideia do pack.\n"
                "### Total ajustado pelos extras escolhidos."
            )),
            discord.ui.ActionRow(basico)
        ))

    async def abrir_basico(self, interaction: discord.Interaction):
        try:
            await iniciar_fluxo_pack(interaction, "Pack Personalizado")
        except Exception:
            log.exception("Falha ao abrir o fluxo Pack Personalizado")
            if not interaction.response.is_done():
                await interaction.response.send_message("Não consegui abrir o pedido. Tente novamente.", ephemeral=True)

async def criar_pack_personalizado(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("Apenas o administrador pode publicar esse painel.", ephemeral=True)
        return
    if interaction.guild is None or not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("Use este comando em um canal de texto do servidor.", ephemeral=True)
        return

    try:
        # O painel inicial é público, igual ao painel /samples.
        await interaction.response.defer(ephemeral=True, thinking=True)
        mensagem = await interaction.channel.send(view=PackPersonalizadoView())
        await interaction.edit_original_response(
            content=f"✅ Painel de Pack Personalizado publicado em {interaction.channel.mention}.",
            view=None
        )
        log.info("Painel Pack Personalizado publicado: canal=%s mensagem=%s", mensagem.channel.id, mensagem.id)
    except Exception as exc:
        log.exception("Falha ao publicar painel Pack Personalizado")
        if interaction.response.is_done():
            await interaction.edit_original_response(
                content=f"❌ Não consegui publicar o painel ({type(exc).__name__}). Verifique as permissões do bot.",
                view=None
            )
        else:
            await interaction.response.send_message("Não consegui publicar o painel. Verifique as permissões do bot.", ephemeral=True)

ROLE_NAMES = ("Fundador", "Staff", "Parceiro", "DJ / Produtor", "Cliente", "Membro")

# Cores oficiais dos cargos do C.C (RGB).
ROLE_COLORS = {
    "fundador": (255, 190, 45),       # dourado
    "staff": (237, 66, 69),           # vermelho
    "parceiro": (46, 134, 222),       # azul
    "dj / produtor": (155, 89, 182),  # roxo
    "cliente": (46, 204, 113),        # verde
    "membro": (26, 188, 156),         # turquesa
}

async def garantir_cargos(guild):
    if guild is None:
        return {}
    roles = {role.name.casefold(): role for role in guild.roles}
    for name in ROLE_NAMES:
        key = name.casefold()
        color = discord.Colour.from_rgb(*ROLE_COLORS[key])
        role = roles.get(key)
        try:
            if role is None:
                role = await guild.create_role(
                    name=name,
                    color=color,
                    reason="Configuração de cargos coloridos do CC BOT"
                )
                roles[key] = role
            elif role.color.value != color.value:
                # Atualiza somente a cor; mantém nome e permissões existentes.
                await role.edit(
                    color=color,
                    reason="Padronização das cores dos cargos do CC BOT"
                )
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Não foi possível criar/colorir o cargo %s", name)
            break
    return roles

class Cargos(app_commands.Group):
    def __init__(self):
        super().__init__(name="cargos", description="Configurar e atribuir cargos da comunidade")

    @app_commands.command(name="configurar", description="Criar os cargos padrão do C.C")
    async def configurar(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode configurar os cargos.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("Use no servidor.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        roles = await garantir_cargos(interaction.guild)
        faltam = [name for name in ROLE_NAMES if name.casefold() not in roles]
        if faltam:
            await interaction.followup.send("Não consegui criar todos. Confira a permissão **Gerenciar cargos** e a hierarquia do bot.", ephemeral=True)
        else:
            await interaction.followup.send("Cargos conferidos/criados: " + ", ".join(ROLE_NAMES), ephemeral=True)

    @app_commands.command(name="cliente", description="Atribuir Cliente após confirmar pagamento")
    async def cliente(self, interaction: discord.Interaction, membro: discord.Member):
        await self.atribuir(interaction, membro, "Cliente")

    @app_commands.command(name="parceiro", description="Atribuir Parceiro após aprovar parceria")
    async def parceiro(self, interaction: discord.Interaction, membro: discord.Member):
        await self.atribuir(interaction, membro, "Parceiro")

    @app_commands.command(name="dj-produtor", description="Atribuir DJ / Produtor")
    async def dj_produtor(self, interaction: discord.Interaction, membro: discord.Member):
        await self.atribuir(interaction, membro, "DJ / Produtor")

    @app_commands.command(name="staff", description="Atribuir Staff")
    async def staff(self, interaction: discord.Interaction, membro: discord.Member):
        await self.atribuir(interaction, membro, "Staff")

    async def atribuir(self, interaction, membro, nome):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode atribuir cargos.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("Use no servidor.", ephemeral=True)
            return
        roles = await garantir_cargos(interaction.guild)
        role = roles.get(nome.casefold())
        if role is None:
            await interaction.response.send_message("Não consegui localizar o cargo. Confira as permissões do bot.", ephemeral=True)
            return
        try:
            await membro.add_roles(role, reason="Atribuição pelo administrador do CC BOT")
            await interaction.response.send_message("Cargo **" + nome + "** atribuído a " + membro.mention + ".", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Sem permissão. Coloque o cargo do bot acima do cargo que deseja atribuir.", ephemeral=True)
        except discord.HTTPException:
            log.exception("Falha ao atribuir %s a %s", nome, membro.id)
            await interaction.response.send_message("O Discord não aceitou a alteração. Tente novamente.", ephemeral=True)

bot.tree.add_command(Cargos())

# Checkout Pix direto: QR Code por compra e registro local de cada pedido.
PIX_ORDERS_FILE = CONFIG / "pix_pedidos.json"
pix_orders = load_json(PIX_ORDERS_FILE, {})
if not isinstance(pix_orders, dict):
    pix_orders = {}
    save_json(PIX_ORDERS_FILE, pix_orders)

def pix_tlv(campo: str, valor: str) -> str:
    tamanho = len(valor.encode("utf-8"))
    if tamanho > 99:
        raise ValueError(f"Campo Pix excede o limite: {campo}")
    return f"{campo}{tamanho:02d}{valor}"

def pix_crc16(texto: str) -> str:
    crc = 0xFFFF
    for byte in texto.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"

def pix_texto_ascii(texto: str, limite: int) -> str:
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^A-Za-z0-9 .'-]", "", texto).strip().upper()
    texto = re.sub(r"\s+", " ", texto)
    if not texto:
        raise ValueError("Nome do recebedor e cidade não podem ficar vazios.")
    return texto[:limite]

def gerar_payload_pix(chave: str, nome: str, cidade: str, valor: float, txid: str) -> str:
    chave = str(chave).strip()
    if not chave:
        raise ValueError("Configure a chave Pix primeiro.")
    if not 0.01 <= float(valor) <= 99999999.99:
        raise ValueError("O valor deve ser maior que zero.")
    nome = pix_texto_ascii(nome, 25)
    cidade = pix_texto_ascii(cidade, 15)
    txid = re.sub(r"[^A-Za-z0-9]", "", str(txid))[:25] or "***"
    conta_pix = pix_tlv("00", "BR.GOV.BCB.PIX") + pix_tlv("01", chave)
    campos = (
        pix_tlv("00", "01")
        + pix_tlv("26", conta_pix)
        + pix_tlv("52", "0000")
        + pix_tlv("53", "986")
        + pix_tlv("54", f"{float(valor):.2f}")
        + pix_tlv("58", "BR")
        + pix_tlv("59", nome)
        + pix_tlv("60", cidade)
        + pix_tlv("62", pix_tlv("05", txid))
    )
    sem_crc = campos + "6304"
    return sem_crc + pix_crc16(sem_crc)

# Checkout unificado do Mercado: QR Pix, canal privado e aprovação manual.
PAYMENT_CATEGORY_NAME = "cc store"
PAYMENT_ADMIN_CATEGORY_NAME = "ANDAMENTO"
PAYMENT_ADMIN_CHANNEL_NAME = "pc-confirmar-pagamentos"

def formatar_reais(valor):
    return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def extrair_txid_da_mensagem(message):
    if not message or not message.embeds:
        return None
    embed = message.embeds[0]
    # A referência técnica fica no rodapé; o campo principal mostra o nome completo.
    rodape = getattr(embed.footer, "text", "") or ""
    encontrado = re.search(r"Referência técnica: ([A-Za-z0-9]+)", rodape)
    if encontrado:
        candidato = encontrado.group(1).upper()
        if candidato in pix_orders:
            return candidato
    for field in embed.fields:
        if field.name.casefold() in ("identificador pix", "código interno do pedido"):
            candidato = re.sub(r"[^A-Za-z0-9]", "", field.value).upper()
            if candidato in pix_orders:
                return candidato
    return None

async def obter_canal_confirmacao(guild):
    canal_id = settings.get("payment_admin_channel_id")
    canal = guild.get_channel(int(canal_id)) if canal_id else None
    admin = guild.get_member(ADMIN_ID)
    if admin is None:
        try:
            admin = await guild.fetch_member(ADMIN_ID)
        except discord.HTTPException:
            admin = None
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    if admin:
        overwrites[admin] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True
        )
    categoria = discord.utils.get(guild.categories, name=PAYMENT_ADMIN_CATEGORY_NAME)
    if categoria is None:
        categoria = await guild.create_category(PAYMENT_ADMIN_CATEGORY_NAME, overwrites=overwrites, reason="Separar pedidos e cancelamentos em ANDAMENTO")
    else:
        await categoria.edit(overwrites=overwrites)
    if not isinstance(canal, discord.TextChannel):
        canal = discord.utils.get(guild.text_channels, name=PAYMENT_ADMIN_CHANNEL_NAME)
    if canal is None:
        canal = await guild.create_text_channel(
            PAYMENT_ADMIN_CHANNEL_NAME, category=categoria, overwrites=overwrites,
            topic="Pedidos Pix aguardando conferência manual do administrador."
        )
    else:
        await canal.edit(overwrites=overwrites, category=categoria)
    settings["payment_admin_channel_id"] = canal.id
    save_json(DATA_FILE, settings)
    return canal

async def obter_canal_cancelados(guild):
    canal_id = settings.get("payment_cancelled_channel_id")
    canal = guild.get_channel(int(canal_id)) if canal_id else None
    admin = guild.get_member(ADMIN_ID)
    if admin is None:
        try:
            admin = await guild.fetch_member(ADMIN_ID)
        except discord.HTTPException:
            admin = None
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    if admin:
        overwrites[admin] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True
        )
    categoria = discord.utils.get(guild.categories, name=PAYMENT_ADMIN_CATEGORY_NAME)
    if categoria is None:
        categoria = await guild.create_category(PAYMENT_ADMIN_CATEGORY_NAME, overwrites=overwrites, reason="Separar pedidos e cancelamentos em ANDAMENTO")
    else:
        await categoria.edit(overwrites=overwrites)
    if not isinstance(canal, discord.TextChannel):
        canal = discord.utils.get(guild.text_channels, name="cpp-cancelamentos")
    if canal is None:
        canal = await guild.create_text_channel(
            "cpp-cancelamentos", category=categoria, overwrites=overwrites,
            topic="Histórico de pedidos Pix cancelados ou negados."
        )
    else:
        await canal.edit(overwrites=overwrites, category=categoria)
    settings["payment_cancelled_channel_id"] = canal.id
    save_json(DATA_FILE, settings)
    return canal

async def criar_pedido_pagamento(interaction, produto, valor, detalhes=None, tipo="digital"):
    if interaction.guild is None:
        raise ValueError("O pagamento precisa ser iniciado dentro do servidor.")
    chave = str(settings.get("pix_key", "")).strip()
    nome_recebedor = str(settings.get("pix_receiver_name", "")).strip()
    cidade = str(settings.get("pix_city", "")).strip()
    if not chave or not nome_recebedor or not cidade:
        raise ValueError("Pix não configurado. Use /pix configurar com chave, nome do recebedor e cidade.")

    user = interaction.user
    nome_usuario = str(getattr(user, "global_name", None) or user.name)
    # Cada cobrança recebe um TXID próprio; compras anteriores nunca bloqueiam uma nova.
    # Mantém parte do nome para facilitar a identificação e acrescenta sufixo aleatório.
    prefixo_txid = re.sub(r"[^A-Za-z0-9]", "", nome_usuario.upper())[:15] or "USUARIO"
    txid = f"{prefixo_txid}{uuid.uuid4().hex[:8].upper()}"
    while txid in pix_orders:
        txid = f"{prefixo_txid}{uuid.uuid4().hex[:8].upper()}"
    payload = gerar_payload_pix(chave, nome_recebedor, cidade, float(valor), txid)
    imagem = qrcode.make(payload)
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    buffer.seek(0)

    guild = interaction.guild
    admin = guild.get_member(ADMIN_ID)
    if admin is None:
        try:
            admin = await guild.fetch_member(ADMIN_ID)
        except discord.HTTPException:
            admin = None

    # Compra de beat: abrir cobrança como tópico privado dentro do canal onde está o painel.
    # Os demais tipos de pedido mantêm o canal privado atual.
    if tipo == "beat" and isinstance(interaction.channel, discord.TextChannel):
        ticket = await interaction.channel.create_thread(
            name=f"cobranca-{user.name[:70]}",
            type=discord.ChannelType.private_thread,
            invitable=False,
            auto_archive_duration=1440,
            reason=f"Checkout Pix do beat; comprador {user.id}"
        )
        await ticket.add_user(user)
        if admin:
            await ticket.add_user(admin)
    else:
        categoria_mercado = discord.utils.get(guild.categories, name=PAYMENT_CATEGORY_NAME)
        if categoria_mercado is None:
            categoria_mercado = await guild.create_category(
                PAYMENT_CATEGORY_NAME, reason="Canais privados de pagamento do Mercado"
            )
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True
            )
        }
        if admin:
            overwrites[admin] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_messages=True, attach_files=True, embed_links=True
            )
        slug = re.sub(r"[^a-z0-9-]", "-", user.name.lower()).strip("-")[:18] or "cliente"
        ticket = await guild.create_text_channel(
            name=f"pagamento-{slug}-{txid[-4:].lower()}",
            category=categoria_mercado, overwrites=overwrites,
            topic=f"Pedido {txid} | Comprador {user.id} | Status: pendente",
            reason=f"Checkout Pix do pedido {txid}"
        )

    registro = {
        "txid": txid, "discord_name": nome_usuario, "discord_id": str(user.id),
        "guild_id": str(guild.id), "ticket_channel_id": str(ticket.id),
        "ticket_is_thread": isinstance(ticket, discord.Thread),
        "product": str(produto)[:100], "amount": round(float(valor), 2),
        "details": str(detalhes or "")[:4000], "fulfillment_type": tipo,
        "status": "aguardando_confirmacao_manual",
        "created_at": datetime.now().isoformat(timespec="seconds")
    }
    pix_orders[txid] = registro
    save_json(PIX_ORDERS_FILE, pix_orders)

    embed = criar_embed_estetico(
        title="Pagamento Pix pendente",
        description=(
            f"Produto: {produto}\nValor: {formatar_reais(valor)}\n"
            f"Comprador: {nome_usuario}\nID Discord: {user.id}\n"
            f"Identificador: {nome_usuario}\n\n"
            "Escaneie o QR Code ou copie o código Pix enviado. Após pagar, aguarde a conferência manual.\n"
            "O pagamento não libera o produto automaticamente."
        ),
        color=discord.Color.orange(), timestamp=discord.utils.utcnow()
    )
    embed.set_image(url="attachment://pix_cc.png")
    if detalhes:
        embed.add_field(name="Detalhes do pedido", value=str(detalhes)[:1024], inline=False)
    embed.set_footer(text=f"Referência técnica: {txid} • Pagamento pendente")
    await ticket.send(
        content="📱 **Pagamento Pix** — escaneie o QR Code ou use o botão abaixo para ver o código copia e cola.\n"
                "Depois de pagar, envie o comprovante neste tópico/canal. A confirmação continua manual.",
        embed=embed,
        file=discord.File(buffer, filename="pix_cc.png"),
        view=CopiarCodigoPixView(payload, txid)
    )

    try:
        canal_admin = await obter_canal_confirmacao(guild)
        aviso = criar_embed_estetico(
            title="🧾 PAGAMENTO PENDENTE — CONFERÊNCIA MANUAL",
            description=(
                f"**Produto:** {produto}\n"
                f"**Valor:** {formatar_reais(valor)}\n"
                f"**Status:** AGUARDANDO CONFERÊNCIA"            ),
            color=discord.Color.orange(), timestamp=discord.utils.utcnow()
        )
        # Dados do comprador em campos próprios para ficarem visíveis no comprovante administrativo.
        aviso.add_field(name="👤 Nome no Discord", value=f"{nome_usuario}\n{user.mention}", inline=True)
        aviso.add_field(name="🆔 ID DO USUÁRIO", value=f"`{user.id}`", inline=True)
        aviso.add_field(name="🔖 IDENTIFICADOR", value=nome_usuario[:1024], inline=False)
        aviso.add_field(name="Tópico/canal privado do pedido", value=ticket.mention, inline=False)
        if detalhes:
            aviso.add_field(name="Resumo do pedido", value=str(detalhes)[:1024], inline=False)
        aviso.set_footer(text=f"Referência técnica: {txid}")
        mensagem_admin = await canal_admin.send(embed=aviso, view=ConfirmarPagamentoView())
        pix_orders[txid]["admin_channel_id"] = str(canal_admin.id)
        pix_orders[txid]["admin_message_id"] = str(mensagem_admin.id)
        save_json(PIX_ORDERS_FILE, pix_orders)
    except Exception:
        log.exception("Falha ao publicar aprovação manual para o pedido %s", txid)
        try:
            await ticket.delete(reason="Não foi possível registrar o pedido na fila administrativa")
        except Exception:
            pass
        pix_orders.pop(txid, None)
        save_json(PIX_ORDERS_FILE, pix_orders)
        raise
    return ticket, txid

class CopiarCodigoPixView(discord.ui.View):
    """Mostra o Pix copia e cola em uma resposta privada para facilitar copiar no celular."""
    def __init__(self, payload: str, txid: str):
        super().__init__(timeout=3600)
        self.payload = payload
        self.txid = txid
        botao = discord.ui.Button(
            label="Copiar código Pix",
            emoji="📋",
            style=discord.ButtonStyle.primary,
            custom_id=f"ccbot:pix:copiar:{txid}"
        )
        botao.callback = self.copiar
        self.add_item(botao)
        comprovante = discord.ui.Button(
            label="Enviar comprovante",
            emoji="📎",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ccbot:pix:comprovante:{txid}"
        )
        comprovante.callback = self.instrucoes_comprovante
        self.add_item(comprovante)

    async def copiar(self, interaction: discord.Interaction):
        # O Discord não permite que bots gravem diretamente na área de transferência.
        # A resposta privada deixa o código isolado, pronto para copiar/segurar no celular.
        await interaction.response.send_message(
            content=f"**Pix copia e cola — pedido {self.txid}**\n"
                    "Toque e segure o código abaixo para copiar tudo:\n"
                    f"```\n{self.payload}\n```",
            ephemeral=True
        )

    async def instrucoes_comprovante(self, interaction: discord.Interaction):
        pedido = pix_orders.get(self.txid)
        if not pedido or str(pedido.get("discord_id")) != str(interaction.user.id):
            await interaction.response.send_message(
                "Este botão pertence a outro pedido.", ephemeral=True
            )
            return
        if pedido.get("status") != "aguardando_confirmacao_manual":
            await interaction.response.send_message(
                "Este pedido não está mais aguardando pagamento.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "📎 **Enviar comprovante sem publicar no chat**\n"
            "Use o comando **/comprovante** e selecione o arquivo no campo **arquivo**.\n"
            "O arquivo será anexado à própria mensagem **PAGAMENTO PENDENTE** em "
            "**Confirmar pagamentos**, junto dos botões de aprovação. Não será criada outra mensagem.\n\n"
            "Aceitamos PNG, JPG, WEBP ou PDF de até 10 MB. "
            "O pagamento só será liberado depois da conferência manual.",
            ephemeral=True
        )

PROCESSING_PIX_ORDERS = set()


class ConfirmarPagamentoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Apenas o administrador autorizado pode confirmar ou cancelar pagamentos.",
                ephemeral=True
            )
            return False
        return True

    async def finalizar_pedido(self, interaction, aprovado):
        txid = extrair_txid_da_mensagem(interaction.message)
        if not txid:
            await interaction.response.send_message(
                "Não consegui localizar o identificador deste pedido. Consulte /pix pedidos.",
                ephemeral=True
            )
            return
        pedido = pix_orders.get(txid)
        if not pedido:
            await interaction.response.send_message("Esse pedido não está registrado.", ephemeral=True)
            return
        if pedido.get("status") != "aguardando_confirmacao_manual":
            await interaction.response.send_message(
                f"Este pedido já foi processado: {pedido.get('status')}.", ephemeral=True
            )
            return
        if txid in PROCESSING_PIX_ORDERS:
            await interaction.response.send_message(
                "Este pedido já está sendo processado. Aguarde um instante.",
                ephemeral=True
            )
            return

        PROCESSING_PIX_ORDERS.add(txid)
        arquivos_cancelados = []
        embed_final = None
        try:
            # Reconhece o clique antes de fazer chamadas de rede ou copiar anexos.
            await interaction.response.defer(ephemeral=True, thinking=True)

            if interaction.message and interaction.message.embeds:
                embed_final = interaction.message.embeds[0]
                embed_final.color = discord.Color.green() if aprovado else discord.Color.red()
                embed_final.description = (embed_final.description or "") + (
                    "\n\nSTATUS FINAL: PAGAMENTO CONFIRMADO MANUALMENTE"
                    if aprovado else "\n\nSTATUS FINAL: PEDIDO CANCELADO / NEGADO"
                )
                embed_final.set_footer(text=f"Identificador: {txid} • Processado por {interaction.user.id}")

            # Primeiro arquiva o pedido cancelado e seus comprovantes. Se isso falhar,
            # o pedido continua pendente e pode ser processado novamente.
            if not aprovado:
                try:
                    if interaction.guild is None or embed_final is None:
                        raise RuntimeError("Não foi possível acessar o embed original do pedido.")
                    canal_cancelados = await obter_canal_cancelados(interaction.guild)
                    for anexo in interaction.message.attachments:
                        arquivos_cancelados.append(await anexo.to_file())
                    if arquivos_cancelados:
                        await canal_cancelados.send(
                            embed=embed_final,
                            files=arquivos_cancelados,
                            allowed_mentions=discord.AllowedMentions.none()
                        )
                    else:
                        await canal_cancelados.send(
                            embed=embed_final,
                            allowed_mentions=discord.AllowedMentions.none()
                        )
                except Exception:
                    log.exception("Falha ao arquivar o pedido cancelado %s; mantendo-o pendente.", txid)
                    await interaction.edit_original_response(
                        content="❌ Não consegui mover o pedido para Cancelados. Ele continua pendente; tente novamente.",
                        view=None
                    )
                    return
                finally:
                    for arquivo_cancelado in arquivos_cancelados:
                        try:
                            arquivo_cancelado.close()
                        except Exception:
                            log.exception("Falha ao liberar arquivo de comprovante arquivado.")

            status_anterior = pedido.get("status")
            processado_anterior = pedido.get("processed_at")
            autor_anterior = pedido.get("processed_by")
            pedido["status"] = "pago_confirmado_manualmente" if aprovado else "cancelado_manualmente"
            pedido["processed_at"] = datetime.now().isoformat(timespec="seconds")
            pedido["processed_by"] = str(interaction.user.id)
            try:
                save_json(PIX_ORDERS_FILE, pix_orders)
            except Exception:
                pedido["status"] = status_anterior
                if processado_anterior is None:
                    pedido.pop("processed_at", None)
                else:
                    pedido["processed_at"] = processado_anterior
                if autor_anterior is None:
                    pedido.pop("processed_by", None)
                else:
                    pedido["processed_by"] = autor_anterior
                raise

            comprador = None
            guild = interaction.guild
            if guild:
                comprador = guild.get_member(int(pedido.get("discord_id", 0)))
            if comprador is None:
                try:
                    comprador = await bot.fetch_user(int(pedido.get("discord_id", 0)))
                except discord.HTTPException:
                    comprador = None

            if comprador:
                if aprovado and pedido.get("fulfillment_type") == "pack":
                    mensagem = (
                        f"Seu pagamento de {formatar_reais(pedido.get('amount', 0))} para {pedido.get('product', 'seu pedido')} foi confirmado. "
                        "Seu pedido personalizado foi aprovado e entrou na fila de produção; o administrador enviará o resultado quando estiver pronto."
                    )
                elif aprovado:
                    mensagem = (
                        f"Seu pagamento de {formatar_reais(pedido.get('amount', 0))} para {pedido.get('product', 'sua compra')} foi confirmado. "
                        "O administrador está finalizando a liberação do pedido."
                    )
                else:
                    mensagem = (
                        f"Seu pedido {pedido.get('product', 'de compra')} foi cancelado. Nenhum produto foi liberado. "
                        "Se você já pagou, entre em contato com a administração."
                    )
                try:
                    await comprador.send(mensagem)
                except discord.HTTPException:
                    log.warning(
                        "Não foi possível enviar o resultado do pedido %s ao PV do usuário %s",
                        txid, pedido.get("discord_id")
                    )

            ticket_id = pedido.get("ticket_channel_id")
            if guild and ticket_id:
                ticket = guild.get_channel(int(ticket_id)) or guild.get_thread(int(ticket_id))
                if ticket is None:
                    try:
                        ticket = await bot.fetch_channel(int(ticket_id))
                    except discord.HTTPException:
                        ticket = None
                if ticket is not None:
                    try:
                        if aprovado and isinstance(ticket, discord.Thread) and comprador:
                            await ticket.send(
                                f"{comprador.mention} **Tudo feito! Olha a sua DM para receber seu produto.**"
                            )

                            async def apagar_topico_depois_de_um_minuto(topico, pedido_id):
                                await asyncio.sleep(60)
                                try:
                                    await topico.delete(
                                        reason=f"Tópico do pedido {pedido_id} encerrado após 1 minuto"
                                    )
                                except discord.HTTPException:
                                    log.exception("Falha ao apagar o tópico do pedido %s após 1 minuto", pedido_id)

                            asyncio.create_task(apagar_topico_depois_de_um_minuto(ticket, txid))
                        else:
                            await ticket.delete(
                                reason=f"Pedido {txid} processado: {'pago' if aprovado else 'cancelado'}"
                            )
                    except discord.HTTPException:
                        log.exception("Falha ao avisar/apagar o tópico/canal do pedido %s", txid)

            if interaction.message:
                try:
                    await interaction.message.delete(
                        reason=f"Pedido {txid} processado: {'pago' if aprovado else 'cancelado'}"
                    )
                except discord.HTTPException:
                    log.exception("Não foi possível remover o pedido processado %s da fila.", txid)
                    if embed_final is not None:
                        try:
                            await interaction.message.edit(embed=embed_final, view=None)
                        except discord.HTTPException:
                            log.exception("Não foi possível marcar o pedido %s como finalizado.", txid)

            await interaction.edit_original_response(
                content=(
                    f"✅ Pagamento de {formatar_reais(pedido.get('amount', 0))} confirmado e removido da fila."
                    if aprovado
                    else "🗂️ Pedido cancelado, arquivado com seus anexos e removido da fila pendente."
                ),
                view=None
            )
        except Exception:
            log.exception("Falha ao finalizar o pedido Pix %s.", txid)
            mensagem_erro = (
                "❌ Ocorreu uma falha ao concluir a operação. Confira o estado do pedido antes de tentar novamente."
            )
            try:
                if interaction.response.is_done():
                    await interaction.edit_original_response(content=mensagem_erro, view=None)
                else:
                    await interaction.response.send_message(mensagem_erro, ephemeral=True)
            except discord.HTTPException:
                log.exception("Não foi possível informar a falha no pedido %s.", txid)
        finally:
            PROCESSING_PIX_ORDERS.discard(txid)

    @discord.ui.button(label="Confirmar pagamento", emoji="✅", style=discord.ButtonStyle.success, custom_id="ccbot:pagamento:confirmar")
    async def confirmar(self, interaction, button):
        await self.finalizar_pedido(interaction, True)

    @discord.ui.button(label="Cancelar pedido", emoji="❌", style=discord.ButtonStyle.danger, custom_id="ccbot:pagamento:cancelar")
    async def cancelar(self, interaction, button):
        await self.finalizar_pedido(interaction, False)

class Pix(app_commands.Group):
    def __init__(self):
        super().__init__(name="pix", description="Gerar Pix e identificar pedidos do CC BOT")

    @app_commands.command(name="configurar", description="Configurar a chave Pix e os dados do recebedor")
    async def configurar(self, interaction: discord.Interaction, chave: str, nome_recebedor: str, cidade: str):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode configurar o Pix.", ephemeral=True)
            return
        try:
            nome = pix_texto_ascii(nome_recebedor, 25)
            local = pix_texto_ascii(cidade, 15)
            if not chave.strip():
                raise ValueError("A chave Pix não pode ficar vazia.")
        except ValueError as exc:
            await interaction.response.send_message(f"Configuração inválida: {exc}", ephemeral=True)
            return
        settings["pix_key"] = chave.strip()
        settings["pix_receiver_name"] = nome
        settings["pix_city"] = local
        save_json(DATA_FILE, settings)
        await interaction.response.send_message(
            "Pix configurado. A chave foi salva localmente em config/config.json, não no código do GitHub.",
            ephemeral=True
        )

    @app_commands.command(name="gerar", description="Gerar QR Code Pix para uma compra individual")
    async def gerar(
        self,
        interaction: discord.Interaction,
        valor: app_commands.Range[float, 0.01, 999999.99],
        produto: app_commands.Range[str, 1, 80]
    ):
        if interaction.guild is None:
            await interaction.response.send_message("Use este comando dentro do servidor.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            ticket, txid = await criar_pedido_pagamento(
                interaction, str(produto), float(valor), "Compra iniciada pelo comando /pix gerar", tipo="digital"
            )
            await interaction.edit_original_response(
                content=(
                    f"Pagamento pendente para **{produto}** ({formatar_reais(valor)}). "
                    f"Acesse o canal privado {ticket.mention}. Identificador: {txid}. "
                    "Aguarde a confirmação manual do administrador."
                ),
                view=None
            )
        except Exception as exc:
            log.exception("Falha ao abrir checkout Pix pelo comando")
            await interaction.edit_original_response(
                content=f"Não consegui criar o pagamento: {str(exc)[:500]}",
                view=None
            )

    @app_commands.command(name="pedidos", description="Listar os pedidos Pix recentes")
    async def pedidos(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode consultar os pedidos.", ephemeral=True)
            return
        recentes = sorted(pix_orders.values(), key=lambda item: item.get("created_at", ""), reverse=True)[:10]
        if not recentes:
            await interaction.response.send_message("Ainda não há pedidos Pix registrados.", ephemeral=True)
            return
        linhas = []
        for pedido in recentes:
            linhas.append(
                f"{pedido.get('txid', '?')} — {pedido.get('discord_name', '?')} "
                f"(ID {pedido.get('discord_id', '?')}) — {pedido.get('product', '?')} — "
                f"R$ {float(pedido.get('amount', 0)):.2f} — {pedido.get('status', '?')}"
            )
        await interaction.response.send_message("\n".join(linhas)[:1900], ephemeral=True)

    @app_commands.command(name="confirmar", description="Marcar manualmente um pedido Pix como pago")
    async def confirmar(self, interaction: discord.Interaction, identificador: str):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode confirmar pagamentos.", ephemeral=True)
            return
        pedido = pix_orders.get(identificador.strip().upper())
        if not pedido:
            await interaction.response.send_message("Não encontrei esse identificador Pix.", ephemeral=True)
            return
        pedido["status"] = "pago_confirmado_manualmente"
        pedido["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
        pedido["confirmed_by"] = str(interaction.user.id)
        save_json(PIX_ORDERS_FILE, pix_orders)
        await interaction.response.send_message(
            f"Pedido {identificador.upper()} marcado como pago. Confira o recebimento no Nubank antes de confirmar.",
            ephemeral=True
        )

bot.tree.add_command(Pix())

@bot.tree.command(name="status", description="Verificar se o CC BOT está funcionando")
async def status(interaction: discord.Interaction):
    if await deny_unauthorized(interaction):
        return
    await interaction.response.send_message(
        f"CC BOT está online.\nLatência: {round(bot.latency * 1000)} ms",
        ephemeral=True
    )

@bot.tree.command(
    name="embedcomponenty2",
    description="Criar uma mensagem Discord Components V2 com container, separador, imagem e botão"
)
@app_commands.describe(
    titulo="Título do componente",
    descricao="Texto principal em Markdown",
    cor="Cor hexadecimal do container, exemplo #5865F2",
    imagem_url="URL pública da imagem (opcional)",
    botao_texto="Texto do botão com link (opcional)",
    botao_url="URL aberta pelo botão (opcional)",
    separador="Mostrar separador visual"
)
async def embedcomponenty2(
    interaction: discord.Interaction,
    titulo: str,
    descricao: str,
    cor: str = "#5865F2",
    imagem_url: str = None,
    botao_texto: str = None,
    botao_url: str = None,
    separador: bool = True
):
    """Cria uma mensagem Components V2. Disponível apenas ao administrador configurado."""
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador autorizado pode criar componentes.",
            ephemeral=True
        )
        return

    titulo = titulo.strip()
    descricao = descricao.strip()
    if not titulo or not descricao:
        await interaction.response.send_message(
            "Título e descrição não podem ficar vazios.",
            ephemeral=True
        )
        return
    if len(titulo) > 200 or len(descricao) > 3500:
        await interaction.response.send_message(
            "Limite excedido: título até 200 caracteres e descrição até 3.500.",
            ephemeral=True
        )
        return

    try:
        cor_hex = cor.strip().removeprefix("#")
        if len(cor_hex) not in (3, 6) or not re.fullmatch(r"[0-9a-fA-F]+", cor_hex):
            raise ValueError
        if len(cor_hex) == 3:
            cor_hex = "".join(caractere * 2 for caractere in cor_hex)
        cor_rgb = int(cor_hex, 16)
    except (AttributeError, ValueError):
        await interaction.response.send_message(
            "Cor inválida. Use hexadecimal, por exemplo #5865F2.",
            ephemeral=True
        )
        return

    if bool(botao_texto) != bool(botao_url):
        await interaction.response.send_message(
            "Para criar botão, preencha **botao_texto** e **botao_url** juntos.",
            ephemeral=True
        )
        return

    def url_http_valida(valor):
        try:
            parsed = urlparse(valor.strip())
            return parsed.scheme in ("http", "https") and bool(parsed.netloc)
        except (AttributeError, ValueError):
            return False

    if imagem_url and not url_http_valida(imagem_url):
        await interaction.response.send_message(
            "A imagem precisa ter uma URL pública começando com https:// ou http://.",
            ephemeral=True
        )
        return
    if botao_url and not url_http_valida(botao_url):
        await interaction.response.send_message(
            "O link do botão precisa começar com https:// ou http://.",
            ephemeral=True
        )
        return
    if interaction.channel is None or not hasattr(interaction.channel, "send"):
        await interaction.response.send_message(
            "Execute o comando dentro de um canal de texto do servidor.",
            ephemeral=True
        )
        return

    try:
        componentes = [
            discord.ui.TextDisplay(content=f"# {titulo}"),
            discord.ui.Separator(
                visible=separador,
                spacing=discord.SeparatorSpacing.large
            ),
            discord.ui.TextDisplay(content=descricao)
        ]
        if imagem_url:
            galeria = discord.ui.MediaGallery()
            galeria.add_item(media=imagem_url, description=titulo[:100])
            componentes.append(galeria)

        container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color(cor_rgb)
        )
        if botao_texto and botao_url:
            container.add_item(discord.ui.ActionRow(
                discord.ui.Button(
                    label=botao_texto[:80],
                    style=discord.ButtonStyle.link,
                    url=botao_url
                )
            ))

        layout = discord.ui.LayoutView(timeout=None)
        layout.add_item(container)

        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.channel.send(view=layout)
        await interaction.edit_original_response(
            content="✅ Componente V2 publicado neste canal."
        )
    except (discord.HTTPException, TypeError, ValueError, AttributeError) as exc:
        log.exception("Falha ao criar mensagem Components V2")
        mensagem = str(exc).strip() or type(exc).__name__
        await interaction.edit_original_response(
            content=f"Não consegui publicar o componente: {mensagem[:500]}"
        )

class ListaDJsSelect(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        inicio = view.page * 25
        nomes = mcs[inicio:inicio + 25]
        options = [
            discord.SelectOption(
                label=str(nome)[:100] or "DJ sem nome",
                value=str(inicio + indice),
                default=(str(nome) == view.selected_dj)
            )
            for indice, nome in enumerate(nomes)
        ]
        super().__init__(
            placeholder="Selecione um DJ...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"ccbot:selecionar_dj:{view.page}"
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            indice = int(self.values[0])
            if indice < 0 or indice >= len(mcs):
                await interaction.response.send_message(
                    "Essa opção de DJ não está mais disponível. Abra a lista novamente.",
                    ephemeral=True
                )
                return

            self.parent_view.selected_dj = str(mcs[indice])
            await interaction.response.edit_message(
                view=ListaDJsView(
                    page=self.parent_view.page,
                    selected_dj=self.parent_view.selected_dj
                )
            )
        except Exception as exc:
            log.exception("Falha ao selecionar DJ")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"Não consegui selecionar esse DJ: {exc}",
                    ephemeral=True
                )

class NavegarDJsButton(discord.ui.Button):
    def __init__(self, view, delta):
        self.parent_view = view
        self.delta = delta
        total_paginas = max(1, (len(mcs) + 24) // 25)
        label = "⬅️ Anterior" if delta < 0 else "Próxima ➡️"
        disabled = (
            (delta < 0 and view.page == 0) or
            (delta > 0 and view.page >= total_paginas - 1)
        )
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            disabled=disabled,
            custom_id=f"ccbot:navegar_djs:{view.page}:{delta}"
        )

    async def callback(self, interaction: discord.Interaction):
        nova_pagina = self.parent_view.page + self.delta
        await interaction.response.edit_message(
            view=ListaDJsView(
                page=nova_pagina,
                selected_dj=self.parent_view.selected_dj
            )
        )

class ConfirmarDJButton(discord.ui.Button):
    def __init__(self, view):
        self.parent_view = view
        super().__init__(
            label="Confirmar DJ",
            emoji="🎧",
            style=discord.ButtonStyle.success,
            disabled=not bool(view.selected_dj),
            custom_id="ccbot:confirmar_dj"
        )

    async def callback(self, interaction: discord.Interaction):
        nome = self.parent_view.selected_dj
        if not nome:
            await interaction.response.send_message(
                "Selecione um DJ primeiro.",
                ephemeral=True
            )
            return

        # Confirma a interação imediatamente para evitar "o aplicativo não respondeu".
        try:
            await interaction.response.defer()
            await enviar_pedido_admin(
                f"🎧 **Pedido do gerador de samples**\n"
                f"DJ selecionado: {nome}\n"
                f"Solicitante: {interaction.user} (ID {interaction.user.id})\n"
                f"Servidor: {interaction.guild.name if interaction.guild else 'DM'}"
            )
            view_confirmacao = discord.ui.LayoutView(timeout=300)
            view_confirmacao.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay(
                        content=(
                            "## 🎧 DJ SELECIONADO\n"
                            f"**{discord.utils.escape_markdown(nome)}**\n\n"
                            "✅ Sua escolha foi confirmada e enviada para processamento.\n"
                            "A geração automática dos arquivos de áudio ainda não está integrada."
                        )
                    )
                )
            )
            await interaction.edit_original_response(view=view_confirmacao)
        except Exception as exc:
            log.exception("Falha ao confirmar DJ selecionado: %s", nome)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Não consegui confirmar esse DJ. Tente selecionar novamente.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "Não consegui concluir a confirmação do DJ. Tente novamente.",
                        ephemeral=True
                    )
            except Exception:
                log.exception("Falha ao informar o usuário sobre o erro na confirmação do DJ")

class ListaDJsView(discord.ui.LayoutView):
    def __init__(self, page=0, selected_dj=None):
        super().__init__(timeout=300)
        self.page = max(0, min(page, max(0, (len(mcs) - 1) // 25)))
        self.selected_dj = selected_dj
        total_paginas = max(1, (len(mcs) + 24) // 25)
        inicio = self.page * 25
        nomes = mcs[inicio:inicio + 25]

        conteudo = (
            "## 🎧 GERAR SAMPLES\n"
            "Selecione um DJ na lista e confirme abaixo.\n\n"
            f"**DJs cadastrados:** {len(mcs)}  •  **Página:** {self.page + 1}/{total_paginas}\n"
            "-# Se houver mais de 25 DJs, use os botões de navegação."
        )
        if self.selected_dj:
            conteudo += f"\n\n**Selecionado:** {discord.utils.escape_markdown(self.selected_dj)}"

        container_items = [
            discord.ui.TextDisplay(content=conteudo),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small)
        ]
        if nomes:
            container_items.append(discord.ui.ActionRow(ListaDJsSelect(self)))
            container_items.append(
                discord.ui.ActionRow(
                    NavegarDJsButton(self, -1),
                    NavegarDJsButton(self, 1)
                )
            )
            container_items.append(discord.ui.ActionRow(ConfirmarDJButton(self)))
        else:
            container_items.append(
                discord.ui.TextDisplay(content="Nenhum DJ cadastrado. Peça ao administrador para adicionar DJs.")
            )

        self.add_item(discord.ui.Container(*container_items))

class AdicionarDJModal(discord.ui.Modal, title="Adicionar DJ"):
    nome = discord.ui.TextInput(
        label="Nome do DJ",
        placeholder="Digite o nome do DJ",
        min_length=1,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode adicionar DJs.", ephemeral=True)
            return

        nome = str(self.nome).strip()
        if not nome:
            await interaction.response.send_message("Digite um nome válido.", ephemeral=True)
            return
        if any(item.casefold() == nome.casefold() for item in mcs):
            await interaction.response.send_message("Esse DJ já está cadastrado.", ephemeral=True)
            return

        mcs.append(nome)
        mcs.sort(key=str.casefold)
        save_json(DJS_FILE, mcs)
        await interaction.response.send_message(
            f"DJ **{discord.utils.escape_markdown(nome)}** adicionado à lista.",
            ephemeral=True
        )
        await asyncio.sleep(2)
        try:
            await interaction.delete_original_response()
        except (discord.NotFound, discord.HTTPException):
            pass

class AlterarDJModal(discord.ui.Modal, title="Alterar nome de DJ"):
    antigo = discord.ui.TextInput(
        label="Nome atual do DJ",
        placeholder="Digite o nome atual exatamente",
        min_length=1,
        max_length=100
    )
    novo = discord.ui.TextInput(
        label="Novo nome do DJ",
        placeholder="Digite o novo nome",
        min_length=1,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode alterar DJs.", ephemeral=True)
            return

        antigo = str(self.antigo).strip()
        novo = str(self.novo).strip()
        indice = next((i for i, item in enumerate(mcs) if item.casefold() == antigo.casefold()), None)
        if indice is None:
            await interaction.response.send_message("Não encontrei esse DJ na lista.", ephemeral=True)
            return
        if any(item.casefold() == novo.casefold() for i, item in enumerate(mcs) if i != indice):
            await interaction.response.send_message("Já existe um DJ com esse nome.", ephemeral=True)
            return

        mcs[indice] = novo
        mcs.sort(key=str.casefold)
        save_json(DJS_FILE, mcs)
        await interaction.response.send_message(
            f"Nome alterado: **{discord.utils.escape_markdown(antigo)}** → **{discord.utils.escape_markdown(novo)}**.",
            ephemeral=True
        )
        await asyncio.sleep(2)
        try:
            await interaction.delete_original_response()
        except (discord.NotFound, discord.HTTPException):
            pass

class ExcluirDJSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=nome[:100], value=nome) for nome in mcs[:25]]
        super().__init__(
            placeholder="Escolha o DJ para excluir...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ccbot:excluir_dj"
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode excluir DJs.", ephemeral=True)
            return
        nome = self.values[0]
        if nome not in mcs:
            await interaction.response.send_message("Esse DJ não está mais cadastrado.", ephemeral=True)
            return
        mcs.remove(nome)
        save_json(DJS_FILE, mcs)
        await interaction.response.send_message(
            f"DJ **{discord.utils.escape_markdown(nome)}** excluído.",
            ephemeral=True
        )
        await asyncio.sleep(2)
        try:
            await interaction.delete_original_response()
        except (discord.NotFound, discord.HTTPException):
            pass

class ExcluirDJView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=300)
        if mcs:
            self.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay(content="## 🗑️ EXCLUIR DJ\nSelecione o DJ que deseja remover da lista."),
                    discord.ui.ActionRow(ExcluirDJSelect())
                )
            )

class AdicionarDJButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Adicionar DJ", style=discord.ButtonStyle.success, custom_id="ccbot:adicionar_djs")

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar esse painel.", ephemeral=True)
            return
        await interaction.response.send_modal(AdicionarDJModal())

class AlterarDJButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Alterar nome", style=discord.ButtonStyle.primary, custom_id="ccbot:alterar_dj")

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar esse painel.", ephemeral=True)
            return
        if not mcs:
            await interaction.response.send_message("Ainda não há DJs cadastrados.", ephemeral=True)
            return
        await interaction.response.send_modal(AlterarDJModal())

class ExcluirDJButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Excluir DJ", style=discord.ButtonStyle.danger, custom_id="ccbot:botao_excluir_dj")

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar esse painel.", ephemeral=True)
            return
        if not mcs:
            await interaction.response.send_message("Ainda não há DJs cadastrados.", ephemeral=True)
            return
        await interaction.response.send_message(view=ExcluirDJView(), ephemeral=True)

class TestarBoasVindasButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Testar boas-vindas",
            emoji="👋",
            style=discord.ButtonStyle.primary,
            custom_id="ccbot:testar_boas_vindas"
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Apenas o administrador pode testar as boas-vindas.",
                ephemeral=True
            )
            return

        try:
            channel = bot.get_channel(WELCOME_CHANNEL_ID)
            if channel is None:
                channel = await bot.fetch_channel(WELCOME_CHANNEL_ID)

            member = interaction.user
            embed = criar_embed_estetico(
                title="Seja bem-vindo(a)",
                description=(
                    "**Eaee, suave? Seja bem-vindo ao nosso server!**\n\n"
                    f"Esperamos que você curta a comunidade, {member.mention}.\n\n"
                    "**Veja as regras do nosso server:**\n"
                    "[▢𝂽﹒regras](https://discord.com/channels/1561513043886743562/1561513274737168414)"
                ),
                color=discord.Color.green()
            )
            embed.set_author(
                name=member.display_name,
                icon_url=member.display_avatar.url
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)
            await interaction.response.send_message(
                f"Teste enviado em <#{WELCOME_CHANNEL_ID}>.",
                ephemeral=True
            )
        except Exception as exc:
            log.exception("Falha no teste de boas-vindas")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"Não consegui enviar o teste: {exc}",
                    ephemeral=True
                )

class VoltarPainelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Voltar ao painel", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="ccbot:painel_voltar")

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=criar_embed_painel(),
            view=PainelAdminView()
        )

class AdicionarEmojisPainelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Enviar ZIP ou imagens", emoji="😀", style=discord.ButtonStyle.success, custom_id="ccbot:painel_adicionar_emojis")

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        await adicionar_emojis(interaction)

class DefinirCanalAvisosSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Escolha o canal de avisos...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            custom_id="ccbot:painel_canal_avisos"
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        canal = self.values[0]
        settings["notice_channel"] = canal.id
        save_json(DATA_FILE, settings)
        await interaction.response.send_message(
            f"Canal de avisos definido para {canal.mention}.",
            ephemeral=True
        )

class BackupPainelButton(discord.ui.Button):
    def __init__(self, destino: str):
        self.destino = destino
        label = "Salvar no Termux" if destino == "termux" else "Enviar por DM"
        super().__init__(label=label, emoji="💾" if destino == "termux" else "📩", style=discord.ButtonStyle.primary, custom_id=f"ccbot:painel_backup_{destino}")

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
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
                    if path.name in {"token.txt", ".env"} or path.suffix in {".pyc", ".log"}:
                        continue
                    z.write(path, relative.as_posix())

            if self.destino == "termux":
                await interaction.followup.send(f"Backup criado no Termux: {arquivo}", ephemeral=True)
            else:
                user = await bot.fetch_user(ADMIN_ID)
                await user.send(
                    f"Backup do CC BOT — {timestamp}. O token não está incluído.",
                    file=discord.File(arquivo)
                )
                await interaction.followup.send("Backup enviado por mensagem privada.", ephemeral=True)
        except Exception as exc:
            log.exception("Falha ao criar backup pelo painel")
            await notify_admin(f"Falha ao criar backup pelo painel: {exc}")
            await interaction.followup.send("Não consegui criar o backup. Verifique os avisos do bot.", ephemeral=True)

class PainelFerramentasView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(BackupPainelButton("termux"))
        self.add_item(BackupPainelButton("discord"))
        self.add_item(VoltarPainelButton())

class PainelEmojisView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(AdicionarEmojisPainelButton())
        self.add_item(VoltarPainelButton())

class PainelCanalAvisosView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(DefinirCanalAvisosSelect())
        self.add_item(VoltarPainelButton())

class ConfirmarLimpezaPVButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Confirmar limpeza",
            emoji="🧹",
            style=discord.ButtonStyle.danger,
            custom_id="ccbot:confirmar_limpeza_pv"
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Apenas o administrador pode usar esta função.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        apagadas = 0
        falhas = 0
        try:
            administrador = bot.get_user(ADMIN_ID) or await bot.fetch_user(ADMIN_ID)
            dm = await administrador.create_dm()
            async for mensagem in dm.history(limit=500):
                if mensagem.author.id != bot.user.id:
                    continue
                try:
                    await mensagem.delete()
                    apagadas += 1
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    falhas += 1

            texto = f"🧹 Limpeza concluída. Mensagens do CC BOT apagadas: **{apagadas}**."
            if falhas:
                texto += f"\nNão foi possível apagar {falhas} mensagem(ns)."
            await interaction.followup.send(texto, ephemeral=True)
            await interaction.message.edit(
                embed=criar_embed_estetico(
                    title="🧹 Limpeza concluída",
                    description=texto,
                    color=discord.Color.green()
                ),
                view=VoltarPainelButtonView()
            )
        except Exception as exc:
            log.exception("Falha ao limpar mensagens do CC BOT no PV")
            await interaction.followup.send(
                f"Não consegui limpar o PV ({type(exc).__name__}).",
                ephemeral=True
            )

class CancelarLimpezaPVButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Cancelar",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id="ccbot:cancelar_limpeza_pv"
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=criar_embed_painel(),
            view=PainelAdminView()
        )

class LimparPVView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(ConfirmarLimpezaPVButton())
        self.add_item(CancelarLimpezaPVButton())

class VoltarPainelButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(VoltarPainelButton())

# Sistema de ícones: importação manual por ZIP, sem monitoramento de pastas.
ICONS_CHANNEL_ID = 1561513290473934998
ICON_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ICON_MAX_FILES = 500
ICON_MAX_UNPACKED_BYTES = 100 * 1024 * 1024
ICON_MAX_FILE_BYTES = 20 * 1024 * 1024
ICON_ZIP_PENDING = {}

def icon_state():
    return settings.setdefault("icon_system", {"albums": {}})

def icon_safe_name(value):
    nome = re.sub(r"\s+", " ", str(value or "")).strip()
    if not nome:
        raise ValueError("Digite o nome da pasta/ícone.")
    return nome[:80]

def extrair_imagens_zip(dados_zip):
    imagens = []
    total_bytes = 0
    with zipfile.ZipFile(io.BytesIO(dados_zip), "r") as arquivo_zip:
        membros = sorted(arquivo_zip.infolist(), key=lambda item: item.filename.casefold())
        for membro in membros:
            if membro.is_dir():
                continue
            caminho = Path(membro.filename.replace("\\", "/"))
            if caminho.name.startswith(".") or caminho.suffix.lower() not in ICON_EXTENSIONS:
                continue
            if membro.file_size <= 0:
                continue
            if membro.file_size > ICON_MAX_FILE_BYTES:
                raise ValueError(f"{caminho.name} ultrapassa 20 MB descompactado.")
            total_bytes += membro.file_size
            if total_bytes > ICON_MAX_UNPACKED_BYTES:
                raise ValueError("O ZIP passa do limite de 100 MB descompactados.")
            if len(imagens) >= ICON_MAX_FILES:
                raise ValueError("O ZIP contém mais de 500 imagens.")
            conteudo = arquivo_zip.read(membro)
            if not conteudo:
                continue
            nome = re.sub(r"[^A-Za-z0-9._ -]", "_", caminho.name)[:180] or f"icon_{len(imagens) + 1}.png"
            imagens.append((nome, conteudo))
    if not imagens:
        raise ValueError("Não encontrei JPG, PNG, WEBP ou GIF dentro do ZIP.")
    return imagens

async def enviar_lote_imagens(destino, imagens, nome, descricao=None, primeira=False, view=None):
    enviados = []
    for inicio in range(0, len(imagens), 10):
        lote = imagens[inicio:inicio + 10]
        arquivos = [discord.File(io.BytesIO(dados), filename=arquivo_nome) for arquivo_nome, dados in lote]
        embed = criar_embed_estetico(
            title=f"🖼️ Icon - {nome}",
            description=descricao if inicio == 0 else None,
            color=discord.Color.from_rgb(0, 0, 0)
        )
        embed.set_footer(text="CC BOT • Icons")
        if primeira and inicio == 0 and lote:
            embed.set_image(url=f"attachment://{lote[0][0]}")
        try:
            mensagem = await destino.send(
                embed=embed if inicio == 0 else None,
                files=arquivos,
                view=view if inicio == 0 else None
            )
            enviados.append(mensagem)
        finally:
            for arquivo in arquivos:
                try:
                    arquivo.close()
                except Exception:
                    pass
    return enviados

async def publicar_zip_icone(nome, imagens, modo):
    if not imagens:
        raise ValueError("O ZIP não contém imagens válidas.")
    canal = bot.get_channel(ICONS_CHANNEL_ID)
    if canal is None:
        canal = await bot.fetch_channel(ICONS_CHANNEL_ID)
    if not isinstance(canal, discord.TextChannel):
        raise TypeError("O canal configurado para ícones não é um canal de texto.")

    nome = icon_safe_name(nome)
    chave = nome.casefold()
    state = icon_state()
    albuns = state.setdefault("albums", {})
    registro = albuns.get(chave, {})
    thread = None
    thread_id = registro.get("thread_id") if isinstance(registro, dict) else None
    if thread_id:
        try:
            thread = bot.get_channel(int(thread_id))
            if thread is None:
                thread = await bot.fetch_channel(int(thread_id))
            if not isinstance(thread, discord.Thread):
                thread = None
            elif thread.archived:
                await thread.edit(archived=False)
        except (discord.NotFound, discord.HTTPException, TypeError, ValueError):
            log.warning("Tópico salvo para '%s' não está acessível; será criado outro.", nome)
            thread = None

    if modo == "single":
        primeira_nome, primeira_dados = imagens[0]
        arquivo_capa = discord.File(io.BytesIO(primeira_dados), filename=primeira_nome)
        embed = criar_embed_estetico(
            title=f"🖼️ Icon - {nome}",
            description=f"📸 {len(imagens)} foto(s) • Abra Ver icons para acessar o tópico.",
            color=discord.Color.from_rgb(0, 0, 0)
        )
        embed.set_image(url=f"attachment://{primeira_nome}")
        embed.set_footer(text="CC BOT • Icons")
        try:
            postagem = await canal.send(embed=embed, file=arquivo_capa, view=IconVerIconsView())
        finally:
            arquivo_capa.close()
        if thread is None:
            thread = await postagem.create_thread(name=f"Icon - {nome}"[:100])
    else:
        postagens = await enviar_lote_imagens(
            canal, imagens, nome,
            descricao=f"Álbum • {len(imagens)} foto(s)",
            primeira=True,
            view=None
        )
        if thread is None:
            thread = await postagens[0].create_thread(name=f"Icon - {nome}"[:100])

    await enviar_lote_imagens(
        thread, imagens, nome,
        descricao=f"Nova importação • {len(imagens)} foto(s)",
        primeira=True,
        view=None
    )
    albuns[chave] = {
        "name": nome,
        "thread_id": thread.id,
        "post_mode": modo,
        "last_import_count": len(imagens),
        "updated_at": datetime.now().isoformat(timespec="seconds")
    }
    save_json(DATA_FILE, settings)
    return thread, len(imagens)

class IconNomeModal(discord.ui.Modal, title="Nome da pasta de ícones"):
    nome = discord.ui.TextInput(
        label="Nome da pasta / ícone",
        placeholder="Ex.: brocasito",
        min_length=1,
        max_length=80
    )

    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode importar ícones.", ephemeral=True)
            return
        pendente = ICON_ZIP_PENDING.get(self.user_id)
        if not pendente:
            await interaction.response.send_message(
                "Os arquivos não estão mais disponíveis. Abra o formulário e envie novamente.",
                ephemeral=True
            )
            return
        try:
            pendente["name"] = icon_safe_name(self.nome.value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"Nome: {pendente['name']}\nAgora escolha como publicar este lote:",
            view=IconModoPublicacaoView(self.user_id),
            ephemeral=True
        )

class IconUploadModal(discord.ui.Modal, title="Enviar arquivos de ícones"):
    arquivos = discord.ui.Label(
        text="ZIP ou imagens (até 10 arquivos)",
        description="Envie um ZIP ou até 10 imagens JPG, PNG, WEBP ou GIF.",
        component=discord.ui.FileUpload(
            custom_id="ccbot_icons_files",
            min_values=1,
            max_values=10,
            required=True
        )
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode importar ícones.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        anexos = self.arquivos.component.values
        if not anexos:
            await interaction.followup.send("Selecione um ZIP ou imagens no formulário.", ephemeral=True)
            return

        pasta_lote = BASE / "uploads" / "icons" / f"{interaction.user.id}_{uuid.uuid4().hex}"
        pasta_imagens = pasta_lote / "images"
        pasta_imagens.mkdir(parents=True, exist_ok=True)
        imagens_salvas = []
        total_recebido = 0
        total_imagens_bytes = 0

        try:
            for anexo in anexos:
                extensao = Path(anexo.filename).suffix.lower()
                if extensao != ".zip" and extensao not in ICON_EXTENSIONS:
                    continue
                if anexo.size > ICON_MAX_UNPACKED_BYTES:
                    raise ValueError(f"{Path(anexo.filename).name} é maior que 100 MB.")
                dados = await anexo.read(use_cached=False)
                total_recebido += len(dados)
                if total_recebido > ICON_MAX_UNPACKED_BYTES:
                    raise ValueError("O total dos arquivos enviados ultrapassa 100 MB.")

                nome_original = Path(anexo.filename).name.replace("/", "_").replace(chr(92), "_")
                if extensao == ".zip":
                    (pasta_lote / f"original_{nome_original}").write_bytes(dados)
                    extraidas = await asyncio.to_thread(extrair_imagens_zip, dados)
                    for nome, conteudo in extraidas:
                        if len(conteudo) > ICON_MAX_FILE_BYTES:
                            raise ValueError(f"{Path(nome).name} ultrapassa 20 MB.")
                        total_imagens_bytes += len(conteudo)
                        if total_imagens_bytes > ICON_MAX_UNPACKED_BYTES:
                            raise ValueError("As imagens descompactadas ultrapassam 100 MB no total.")
                        destino = pasta_imagens / f"{len(imagens_salvas) + 1:04d}_{Path(nome).name}"
                        destino.write_bytes(conteudo)
                        imagens_salvas.append(destino)
                else:
                    if len(dados) > ICON_MAX_FILE_BYTES:
                        raise ValueError(f"{nome_original} ultrapassa 20 MB.")
                    total_imagens_bytes += len(dados)
                    if total_imagens_bytes > ICON_MAX_UNPACKED_BYTES:
                        raise ValueError("As imagens ultrapassam 100 MB no total.")
                    destino = pasta_imagens / f"{len(imagens_salvas) + 1:04d}_{nome_original}"
                    destino.write_bytes(dados)
                    imagens_salvas.append(destino)

                if len(imagens_salvas) > ICON_MAX_FILES:
                    raise ValueError("O limite é de 500 imagens por envio.")

            if not imagens_salvas:
                raise ValueError("Envie um ZIP com imagens ou imagens soltas JPG, PNG, WEBP ou GIF.")

            ICON_ZIP_PENDING[interaction.user.id] = {
                "folder": str(pasta_lote),
                "files": [str(path) for path in imagens_salvas],
                "image_count": len(imagens_salvas),
                "name": None
            }
            await interaction.followup.send(
                f"Recebi {len(imagens_salvas)} imagem(ns) e salvei os arquivos na pasta uploads/icons do bot.\n"
                "Agora informe o nome da pasta/ícone e escolha como publicar.",
                view=IconZipRecebidoView(interaction.user.id),
                ephemeral=True
            )
        except (zipfile.BadZipFile, ValueError, OSError) as exc:
            log.warning("Upload de ícones inválido: %s", exc)
            await interaction.followup.send(f"Não consegui processar os arquivos: {str(exc)[:350]}", ephemeral=True)
        except Exception as exc:
            log.exception("Falha no formulário de upload de ícones")
            await interaction.followup.send(f"Falha ao processar o envio ({type(exc).__name__}).", ephemeral=True)

class IconProcessarZipButton(discord.ui.Button):
    def __init__(self, user_id):
        super().__init__(
            label="Definir nome e publicação",
            emoji="🗂️",
            style=discord.ButtonStyle.success,
            custom_id=f"ccbot:icons_name_zip:{user_id}"
        )
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction) or interaction.user.id != self.user_id:
            await interaction.response.send_message("Somente quem enviou os arquivos pode continuar.", ephemeral=True)
            return
        if self.user_id not in ICON_ZIP_PENDING:
            await interaction.response.send_message("Envio expirado. Selecione os arquivos novamente.", ephemeral=True)
            return
        await interaction.response.send_modal(IconNomeModal(self.user_id))

class IconZipRecebidoView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=600)
        self.add_item(IconProcessarZipButton(user_id))

class IconModoPublicacaoView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = user_id

    async def publicar(self, interaction: discord.Interaction, modo):
        if not is_admin(interaction) or interaction.user.id != self.user_id:
            await interaction.response.send_message("Somente quem enviou os arquivos pode continuar.", ephemeral=True)
            return
        pendente = ICON_ZIP_PENDING.get(self.user_id)
        if not pendente or not pendente.get("name"):
            await interaction.response.send_message("Arquivos ou nome não encontrados. Inicie a importação novamente.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            imagens = await asyncio.to_thread(lambda: [(Path(caminho).name, Path(caminho).read_bytes()) for caminho in pendente["files"]])
            thread, total = await publicar_zip_icone(pendente["name"], imagens, modo)
            ICON_ZIP_PENDING.pop(self.user_id, None)
            await interaction.edit_original_response(
                content=(
                    f"✅ Importação concluída\n"
                    f"Ícone: {pendente['name']}\n"
                    f"Fotos: {total}\n"
                    f"Publicação: {'Post único (capa + Ver icons)' if modo == 'single' else 'Post em álbum (todas as fotos)'}\n"
                    f"Tópico permanente: {thread.mention}\n"
                    "Novas importações com o mesmo nome serão adicionadas a esse mesmo tópico."
                ),
                view=None
            )
        except Exception as exc:
            log.exception("Falha ao processar ZIP de ícones")
            await interaction.edit_original_response(
                content=f"Não consegui publicar os arquivos: {type(exc).__name__}: {str(exc)[:350]}",
                view=None
            )

    @discord.ui.button(label="Post único (capa + Ver icons)", emoji="🖼️", style=discord.ButtonStyle.primary)
    async def post_unico(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.publicar(interaction, "single")

    @discord.ui.button(label="Post em álbum", emoji="📚", style=discord.ButtonStyle.secondary)
    async def post_album(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.publicar(interaction, "album")

class IconEnviarZipButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Enviar ZIP ou imagens",
            emoji="📎",
            style=discord.ButtonStyle.success,
            custom_id="ccbot:icons_upload_zip"
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        ICON_ZIP_PENDING.pop(interaction.user.id, None)
        await interaction.response.send_modal(IconUploadModal())

class PainelIconesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(IconEnviarZipButton())
        self.add_item(VoltarPainelButton())

def criar_embed_painel_icones():
    embed = criar_embed_estetico(
        title="🖼️ CENTRAL DE ÍCONES",
        description=(
            "Envie ZIPs ou imagens diretamente pelo formulário, sem monitoramento de pastas.\n\n"
            "### 📦 Como funciona\n"
            "1. Clique em Enviar ZIP ou imagens.\n"
            "2. Anexe um ZIP ou até 10 imagens no formulário.\n"
            "3. Informe o nome da pasta/ícone.\n"
            "4. Escolha o formato da publicação.\n\n"
            "Post único: uma capa com botão Ver icons para abrir o tópico.\n"
            "Post em álbum: todas as fotos no canal, sem botão.\n\n"
            "Importações com o mesmo nome, como brocasito, ficam no mesmo tópico permanente."
        ),
        color=discord.Color.from_rgb(190, 45, 75)
    )
    embed.set_footer(text="CC BOT • Icons")
    return embed

class IconVerIconsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(IconVerIconsButton())

class IconVerIconsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Ver icons",
            emoji="🖼️",
            style=discord.ButtonStyle.primary,
            custom_id="ccbot:icons_view_album"
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.message is None:
            await interaction.response.send_message("Use esse botão dentro do servidor.", ephemeral=True)
            return
        embeds = interaction.message.embeds
        if not embeds or not embeds[0].title:
            await interaction.response.send_message("Não consegui identificar esse ícone.", ephemeral=True)
            return
        nome = embeds[0].title.strip().removeprefix("🖼️ Icon - ").removesuffix(" (continuação)").strip()
        registro = icon_state().setdefault("albums", {}).get(nome.casefold())
        thread_id = registro.get("thread_id") if isinstance(registro, dict) else None
        if not thread_id:
            await interaction.response.send_message("O tópico deste ícone ainda não está registrado.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            thread = bot.get_channel(int(thread_id))
            if thread is None:
                thread = await bot.fetch_channel(int(thread_id))
            if not isinstance(thread, discord.Thread):
                raise TypeError("O destino salvo não é um tópico.")
            if thread.archived:
                await thread.edit(archived=False)
            await interaction.followup.send(f"Tópico permanente de {nome}: {thread.mention}", ephemeral=True)
        except Exception:
            log.exception("Falha ao abrir tópico permanente do ícone '%s'.", nome)
            await interaction.followup.send("Não consegui acessar o tópico desse ícone. Confira as permissões do bot.", ephemeral=True)

class PermissoesCategoriasView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Aplicar permissões", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="ccbot:aplicar_permissoes_categorias")
    async def aplicar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("Use esta função dentro do servidor.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        comunidade = []
        alteradas = []
        falhas = []

        for categoria in guild.categories:
            if categoria.name.strip().casefold() == "community":
                comunidade.append(categoria.name)
                continue
            try:
                overwrites = dict(categoria.overwrites)
                everyone = overwrites.get(guild.default_role, discord.PermissionOverwrite())
                everyone.view_channel = True
                everyone.use_application_commands = True
                everyone.send_messages = False
                everyone.send_messages_in_threads = False
                everyone.create_public_threads = False
                everyone.create_private_threads = False
                overwrites[guild.default_role] = everyone
                await categoria.edit(
                    overwrites=overwrites,
                    reason=f"Configuração pelo CC BOT solicitada por {interaction.user} ({interaction.user.id})"
                )
                alteradas.append(categoria.name)
            except Exception as exc:
                falhas.append(f"{categoria.name}: {type(exc).__name__}")

        resumo = (
            "🔒 **Configuração concluída**\n"
            f"Categorias alteradas: **{len(alteradas)}**\n"
            f"Categorias preservadas: **{', '.join(comunidade) if comunidade else 'nenhuma categoria chamada Comunidade encontrada'}**\n"
            "Membros podem visualizar os canais, usar comandos de aplicativos e interagir com botões, "
            "mas não podem enviar mensagens nem criar tópicos pela permissão geral."
        )
        if falhas:
            resumo += "\n\n⚠️ **Falhas:**\n" + "\n".join(f"• {x}" for x in falhas[:10])
        await interaction.edit_original_response(content=resumo, view=None)

    @discord.ui.button(label="Cancelar", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="ccbot:cancelar_permissoes_categorias")
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Operação cancelada. Nenhuma permissão foi alterada.", embed=None, view=None)

async def abrir_permissoes_categorias(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("Use esta função dentro do servidor.", ephemeral=True)
        return

    quantidade = sum(1 for c in interaction.guild.categories if c.name.strip().casefold() != "community")
    comunidades = [c.name for c in interaction.guild.categories if c.name.strip().casefold() == "community"]
    embed = criar_embed_estetico(
        title="🔒 Permissões das categorias",
        description=(
            f"Vou alterar **{quantidade} categorias**, preservando a categoria "
            f"**Community** ({', '.join(comunidades) if comunidades else 'não encontrada'}).\n\n"
            "Nas demais categorias, @everyone poderá **ver os canais, usar comandos de aplicativos e interagir com botões**, "
            "mas não poderá enviar mensagens nem criar tópicos. As permissões específicas de outros cargos serão mantidas.\n\n"
            "Confirme apenas se deseja aplicar essa mudança ao servidor."
        ),
        color=PAINEL_ADMIN_COLOR
    )
    await interaction.response.edit_message(embed=embed, view=PermissoesCategoriasView())

TRABALHOS_ON_CHANNEL_ID = 1561837934649409696

class TrabalhosOnView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Publicar e marcar todos", emoji="📣", style=discord.ButtonStyle.success, custom_id="ccbot:publicar_trabalhos_on")
    async def publicar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            canal = bot.get_channel(TRABALHOS_ON_CHANNEL_ID)
            if canal is None:
                canal = await bot.fetch_channel(TRABALHOS_ON_CHANNEL_ID)
            if not isinstance(canal, discord.TextChannel):
                raise TypeError("O canal configurado não é um canal de texto.")

            embed = criar_embed_estetico(
                title="🟢  TRABALHOS ESTÃO ON!",
                description=(
                    "Os trabalhos estão **abertos e disponíveis**!\n\n"
                    "Precisa de algum serviço? Confira as opções e abra seu pedido pelos painéis do servidor.\n\n"
                    "✦ **Estamos atendendo.**"
                ),
                color=discord.Color.from_rgb(0, 0, 0),
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text="CC • Central de trabalhos")
            mensagem = await canal.send(
                content="@everyone",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=True)
            )
            await interaction.edit_original_response(
                content=f"✅ Aviso publicado em <#{canal.id}> e enviado com menção a todos.\nMensagem: https://discord.com/channels/{interaction.guild_id}/{canal.id}/{mensagem.id}",
                embed=None,
                view=None
            )
        except Exception as exc:
            log.exception("Falha ao publicar aviso Trabalhos ON")
            await interaction.edit_original_response(
                content=f"❌ Não consegui publicar o aviso. Confira se o bot tem permissão para enviar mensagens, embeds e mencionar @everyone. Erro: {type(exc).__name__}",
                embed=None,
                view=None
            )

    @discord.ui.button(label="Cancelar", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="ccbot:cancelar_trabalhos_on")
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Publicação cancelada. Nenhum aviso foi enviado.", embed=None, view=None)

async def abrir_trabalhos_on(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
        return
    embed = criar_embed_estetico(
        title="📣 Aviso: Trabalhos ON",
        description=(
            f"Vou publicar um embed no canal <#{TRABALHOS_ON_CHANNEL_ID}> informando que os trabalhos estão abertos.\n\n"
            "**A publicação marcará @everyone**, notificando todos os membros conforme as configurações deles.\n\n"
            "Confirme apenas quando quiser enviar o aviso."
        ),
        color=discord.Color.from_rgb(0, 0, 0)
    )
    await interaction.response.edit_message(embed=embed, view=TrabalhosOnView())

def formatar_permissoes_overwrite(canal):
    linhas = []
    for alvo, overwrite in canal.overwrites.items():
        allow, deny = overwrite.pair()
        permitidas = [nome for nome, valor in allow if valor]
        negadas = [nome for nome, valor in deny if valor]
        tipo = "cargo" if isinstance(alvo, discord.Role) else "membro"
        nome_alvo = f"@everyone ({alvo.id})" if isinstance(alvo, discord.Role) and alvo.is_default() else f"{alvo.name} ({alvo.id})"
        linhas.append(f"    - {tipo}: {nome_alvo}")
        linhas.append(f"      permite: {', '.join(permitidas) if permitidas else 'nenhuma explicitamente'}")
        linhas.append(f"      nega: {', '.join(negadas) if negadas else 'nenhuma explicitamente'}")
    return linhas

def gerar_relatorio_hierarquia(guild, canais):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    linhas = [
        "RELATÓRIO DE HIERARQUIA — CC BOT",
        f"Servidor: {guild.name} (ID: {guild.id})",
        f"Gerado em: {agora}",
        f"Categorias: {sum(isinstance(c, discord.CategoryChannel) for c in canais)}",
        f"Canais totais: {sum(not isinstance(c, discord.CategoryChannel) for c in canais)}",
        f"Cargos: {len(guild.roles)}",
        "",
        "=" * 72,
        "1. ESTRUTURA DE CATEGORIAS E CANAIS",
        "=" * 72,
    ]

    categorias = sorted(
        (c for c in canais if isinstance(c, discord.CategoryChannel)),
        key=lambda c: c.position
    )
    canais_sem_categoria = []
    for categoria in categorias:
        linhas.append("")
        linhas.append(f"[CATEGORIA] {categoria.name} | ID: {categoria.id} | posição: {categoria.position}")
        linhas.extend(formatar_permissoes_overwrite(categoria))
        filhos = sorted(
            (c for c in canais if not isinstance(c, discord.CategoryChannel) and c.category_id == categoria.id),
            key=lambda c: (c.position, c.name.casefold())
        )
        if not filhos:
            linhas.append("  (sem canais)")
        for canal in filhos:
            tipo = type(canal).__name__
            extra = ""
            if isinstance(canal, discord.TextChannel):
                extra = f" | NSFW: {canal.nsfw} | lento: {canal.slowmode_delay}s"
            elif isinstance(canal, discord.VoiceChannel):
                extra = f" | bitrate: {canal.bitrate} | limite: {canal.user_limit}"
            linhas.append(f"  [{tipo}] {canal.name} | ID: {canal.id} | posição: {canal.position}{extra}")
            linhas.extend(formatar_permissoes_overwrite(canal))
    canais_sem_categoria = sorted(
        (c for c in canais if not isinstance(c, discord.CategoryChannel) and c.category_id is None),
        key=lambda c: (c.position, c.name.casefold())
    )
    if canais_sem_categoria:
        linhas.append("")
        linhas.append("[CANAIS SEM CATEGORIA]")
        for canal in canais_sem_categoria:
            linhas.append(f"  [{type(canal).__name__}] {canal.name} | ID: {canal.id} | posição: {canal.position}")
            linhas.extend(formatar_permissoes_overwrite(canal))

    linhas.extend(["", "=" * 72, "2. CARGOS E PERMISSÕES GERAIS", "=" * 72, ""])
    for cargo in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        perms = [nome for nome, ativo in cargo.permissions if ativo]
        linhas.append(
            f"[CARGO] {cargo.name} | ID: {cargo.id} | posição: {cargo.position} | "
            f"membros: {len(cargo.members)} | gerenciado pelo Discord/bot: {cargo.managed} | "
            f"exibido separado: {cargo.hoist} | mencionável: {cargo.mentionable}"
        )
        linhas.append(f"  permissões gerais: {', '.join(perms) if perms else 'nenhuma'}")
        linhas.append("")

    linhas.extend([
        "=" * 72,
        "3. NOTAS PARA A ANÁLISE",
        "=" * 72,
        "- Permissões de categoria podem ser sobrescritas por permissões específicas do canal.",
        "- Cargos com permissões gerais elevadas podem manter acesso mesmo quando @everyone é limitado.",
        "- A ordem dos cargos afeta a hierarquia e quais cargos o bot consegue gerenciar.",
        "- Este relatório é somente leitura; nenhuma categoria, canal, cargo ou permissão foi alterado.",
    ])
    return "\n".join(linhas)

async def analisar_hierarquia(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("Apenas o administrador pode usar esta função.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("Abra esta função dentro do servidor.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        guild = interaction.guild
        # Busca a lista atual do Discord para não depender apenas do cache local.
        canais = await guild.fetch_channels()
        relatorio = gerar_relatorio_hierarquia(guild, canais)
        arquivo = discord.File(
            io.BytesIO(relatorio.encode("utf-8")),
            filename=f"hierarquia_{guild.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        administrador = bot.get_user(ADMIN_ID) or await bot.fetch_user(ADMIN_ID)
        await administrador.send(
            content=(
                f"📋 **Relatório de hierarquia de {guild.name}**\n"
                f"Categorias: **{sum(isinstance(c, discord.CategoryChannel) for c in canais)}** • "
                f"Canais: **{sum(not isinstance(c, discord.CategoryChannel) for c in canais)}** • "
                f"Cargos: **{len(guild.roles)}**\n"
                "O arquivo contém a estrutura e as permissões gerais e específicas. "
                "Nenhuma alteração foi feita no servidor."
            ),
            file=arquivo
        )
        await interaction.followup.send(
            "✅ Relatório enviado ao seu PV. Não alterei nenhuma categoria, canal, cargo ou permissão.",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Não consegui enviar o relatório no PV. Ative mensagens diretas do servidor e tente novamente.",
            ephemeral=True
        )
    except Exception as exc:
        log.exception("Falha ao gerar relatório de hierarquia")
        await interaction.followup.send(
            f"❌ Não consegui gerar o relatório ({type(exc).__name__}). Confira os avisos do bot.",
            ephemeral=True
        )

HIERARQUIA_CATEGORIAS = {
    "admin": {"id": 1561513258211352626, "nome": "administration", "posicao": 5},
    "welcome": {"id": 1561513260501569576, "nome": "information", "posicao": 6},
    "lojas": {"id": 1561513261424447609, "nome": "partners", "posicao": 8},
    "mercado": {"id": 1561513263286452275, "nome": "cc store", "posicao": 7},
    "comunidade": {"id": 1561513265006116877, "nome": "community", "posicao": 3},
    "estatisticas": {"id": 1561513258970521743, "nome": "statistics", "posicao": 2},
}

HIERARQUIA_MOVER_CANAIS = [
    (1561513291703128106, 1561513265006116877, 0),  # chat-geral
    (1561513297155592253, 1561513265006116877, 1),  # duvidas
    (1561513293124739173, 1561513265006116877, 2),  # divulgação
    (1561513290473934998, 0, 0),  # icon -> categoria customization criada/localizada pelo nome
    (1561513293934362705, 0, 0),  # packs-gratis -> free things
    (1561513295352037507, 0, 1),  # projetos-gratis -> free things
    (1561513284811624480, 1561513263286452275, 0),  # gerador-de-samples
    (1561513286967500841, 1561513263286452275, 1),  # pack-personalizado
    (1561513288297353286, 1561513263286452275, 2),  # comprar-beats
    (1561513289438199878, 1561513263286452275, 3),  # arrumador-de-projeto
    (1561513284191125625, 1561513263286452275, 4),  # vendas
]

class ConfirmarOrganizacaoHierarquiaView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id

    @discord.ui.button(label="Aplicar organização", emoji="🔄", style=discord.ButtonStyle.success)
    async def aplicar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar esta função.", ephemeral=True)
            return
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("Abra este painel no servidor correto.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            guild = interaction.guild
            organizacao_ok = await aplicar_organizacao_automatica(guild)
            alteradas = await renomear_categorias_solicitadas(guild)
            cargos_alterados = await ordenar_cargos_principais(guild)
            await interaction.edit_original_response(
                content=(
                    "✅ **Organização do servidor aplicada pelo CC BOT.**\n"
                    f"• Organização de categorias/canais: **{'concluída' if organizacao_ok else 'parcial — confira as permissões'}**\n"
                    f"• Categorias renomeadas: **{alteradas}**\n"
                    f"• Cargos reposicionados: **{cargos_alterados}**\n"
                    "Os canais e mensagens existentes foram preservados; nenhum canal foi apagado."
                ),
                embed=None,
                view=None
            )
        except Exception as exc:
            log.exception("Falha ao renomear categorias pelo painel")
            await interaction.edit_original_response(
                content=f"❌ Falha ao renomear categorias ({type(exc).__name__}). Confira o log do CC BOT.",
                embed=None,
                view=None
            )

    @discord.ui.button(label="Cancelar", emoji="✖️", style=discord.ButtonStyle.danger)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode cancelar.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content="Atualização cancelada. Nenhuma alteração foi feita.",
            embed=None,
            view=None
        )

async def abrir_organizacao_hierarquia(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("Apenas o administrador pode usar esta função.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("Abra esta função dentro do servidor.", ephemeral=True)
        return

    embed = criar_embed_estetico(
        title="🗂️ Hierarquia do servidor",
        description=(
            "O CC BOT vai organizar as categorias e os canais conhecidos, padronizar os nomes e ordenar os cargos principais.\n\n"
            "A estrutura prioriza Status, Customização, Sites, Mercado/Vendas, Comunidade e Grátis.\n\n"
            "Canais e mensagens existentes serão preservados; nenhuma categoria ou canal será apagado."
        ),
        color=discord.Color.dark_grey()
    )
    await interaction.response.edit_message(
        embed=embed,
        view=ConfirmarOrganizacaoHierarquiaView(interaction.guild.id)
    )

@bot.tree.command(name="painel", description="Abrir o painel administrativo do CC BOT")
async def painel(interaction: discord.Interaction):
    # Validações simples respondem imediatamente; a montagem do painel ocorre após defer.
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador pode usar este comando.", ephemeral=True
        )
        return
    if interaction.guild is None or interaction.guild.id != 1561513043886743562:
        await interaction.response.send_message(
            "Use este comando dentro do servidor oficial do CC BOT.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        embed = criar_embed_painel()
        view = PainelAdminView()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        log.info("Comando /painel aberto por %s (%s).", interaction.user, interaction.user.id)
    except Exception as exc:
        log.exception("Falha ao abrir /painel")
        try:
            await interaction.followup.send(
                f"Não consegui abrir o painel ({type(exc).__name__}). O erro foi registrado no log.",
                ephemeral=True
            )
        except discord.HTTPException:
            pass

@bot.tree.command(name="hierarquia", description="Abrir o painel para atualizar a hierarquia do servidor")
async def hierarquia(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("Apenas o administrador pode usar este comando.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("Use este comando dentro do servidor.", ephemeral=True)
        return

    embed = criar_embed_estetico(
        title="🗂️ Hierarquia do servidor",
        description=(
            "O CC BOT vai organizar as categorias e os canais conhecidos, padronizar os nomes e ordenar os cargos principais.\n\n"
            "A estrutura prioriza Status, Customização, Sites, Mercado/Vendas, Comunidade e Grátis.\n\n"
            "Canais e mensagens existentes serão preservados; nenhuma categoria ou canal será apagado."
        ),
        color=discord.Color.dark_grey()
    )
    await interaction.response.send_message(
        embed=embed,
        view=ConfirmarOrganizacaoHierarquiaView(interaction.guild.id),
        ephemeral=True
    )

PAINEL_ADMIN_COLOR = discord.Color.from_rgb(35, 37, 42)

PAINEL_GRUPOS = {
    "publicacoes": {
        "titulo": "Publicações e produtos",
        "descricao": "Painéis publicados no servidor e cadastros usados por eles.",
        "categorias": ["publicar", "djs", "parcerias", "trampar_conosco"],
    },
    "bot": {
        "titulo": "Central do bot",
        "descricao": "Tudo que o bot envia, administra ou usa para comunicar-se: boas-vindas, avisos, limpeza de mensagens e funcionamento.",
        "categorias": ["boas_vindas", "trabalhos_on", "limpar_pv", "manutencao", "canal_avisos", "regras"],
    },
    "personalizacao": {
        "titulo": "Personalização",
        "descricao": "Emojis, ícones e imagens usados para personalizar o servidor.",
        "categorias": ["emojis", "icones"],
    },
    "sistema": {
        "titulo": "Sistema e backups",
        "descricao": "Faça cópias de segurança e acesse ferramentas técnicas do bot.",
        "categorias": ["ferramentas"],
    },
    "servidor": {
        "titulo": "Estrutura do servidor",
        "descricao": "Consulte permissões e organize a hierarquia de canais.",
        "categorias": ["permissoes_categorias", "analisar_hierarquia", "organizar_hierarquia"],
    },
}

OPCOES_CATEGORIAS_PAINEL = [
    discord.SelectOption(label="Gerenciar DJs", description="Adicionar, editar ou remover DJs dos painéis", emoji="🎧", value="djs"),
    discord.SelectOption(label="Mensagem de boas-vindas", description="Testar a mensagem enviada aos novos membros", emoji="👋", value="boas_vindas"),
    discord.SelectOption(label="Aviso de trabalhos", description="Publicar aviso de que os trabalhos estão abertos", emoji="📣", value="trabalhos_on"),
    discord.SelectOption(label="Verificar funcionamento", description="Conferir se o bot está online e respondendo", emoji="🛠️", value="manutencao"),
    discord.SelectOption(label="Limpar mensagens privadas", description="Apagar somente mensagens do bot na sua DM", emoji="🧹", value="limpar_pv"),
    discord.SelectOption(label="Canal de avisos do bot", description="Definir onde o bot envia erros e avisos técnicos", emoji="📨", value="canal_avisos"),
    discord.SelectOption(label="Regras", description="Melhorar a aparência da mensagem de regras existente", emoji="📜", value="regras"),
    discord.SelectOption(label="Adicionar emojis", description="Enviar ZIP ou imagens para personalizar o servidor", emoji="😀", value="emojis"),
    discord.SelectOption(label="Adicionar ícones", description="Importar ZIPs ou imagens para publicar", emoji="🖼️", value="icones"),
    discord.SelectOption(label="Backups", description="Salvar cópia no Termux ou enviar por mensagem privada", emoji="💾", value="ferramentas"),
    discord.SelectOption(label="Painéis e produtos", description="Samples, acapellas, arrumador, beats e packs", emoji="📋", value="publicar"),
    discord.SelectOption(label="Parcerias", description="Atualizar o painel de parcerias existente", emoji="🤝", value="parcerias"),
    discord.SelectOption(label="Trampar Conosco", description="Atualizar o painel de trabalhos no canal existente", emoji="🧑‍💻", value="trampar_conosco"),
    discord.SelectOption(label="Permissões dos canais", description="Revisar acesso sem alterar a Comunidade", emoji="🔒", value="permissoes_categorias"),
    discord.SelectOption(label="Consultar hierarquia", description="Enviar relatório de canais, cargos e permissões", emoji="🧭", value="analisar_hierarquia"),
    discord.SelectOption(label="Organizar hierarquia", description="Reordenar canais após confirmação", emoji="🗂️", value="organizar_hierarquia"),
]

async def atualizar_embed_regras(interaction: discord.Interaction):
    """Restiliza a mensagem de regras existente sem recriá-la nem apagar seu conteúdo."""
    message_id = 1563670149150547999
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Abra o painel dentro do servidor.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    canais = list(guild.text_channels)
    for thread in getattr(guild, "threads", []):
        if thread not in canais:
            canais.append(thread)

    mensagem_alvo = None
    canal_alvo = None
    for canal in canais:
        try:
            mensagem_alvo = await canal.fetch_message(message_id)
            canal_alvo = canal
            break
        except discord.NotFound:
            continue
        except discord.Forbidden:
            continue
        except discord.HTTPException:
            log.warning("Não foi possível verificar mensagens no canal %s ao localizar o painel de regras.", canal.id)
            continue

    if mensagem_alvo is None:
        await interaction.followup.send(
            "Não encontrei a mensagem de regras com o ID configurado. Nenhuma mensagem foi criada. "
            "Confira se o bot consegue ler o canal e se esse ID é da mensagem correta.",
            ephemeral=True
        )
        return

    novos_embeds = []
    for original in mensagem_alvo.embeds:
        embed = original.copy()
        embed.color = discord.Color.from_rgb(43, 45, 49)
        if not embed.title:
            embed.title = "📜 REGRAS DA COMUNIDADE"
        if not embed.description and not embed.fields:
            embed.description = "Leia as regras abaixo e siga as orientações para manter a comunidade organizada."
        if not embed.footer.text:
            embed.set_footer(text="C.C ©  •  REGRAS DA COMUNIDADE")
        if not embed.thumbnail.url and not embed.image.url and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        novos_embeds.append(embed)

    if not novos_embeds:
        conteudo = (mensagem_alvo.content or "").strip()
        if not conteudo:
            await interaction.followup.send(
                "A mensagem não tem embed nem texto para estilizar. Não alterei nada.",
                ephemeral=True
            )
            return
        embed = criar_embed_estetico(
            title="📜 REGRAS DA COMUNIDADE",
            description=conteudo,
            color=discord.Color.from_rgb(43, 45, 49)
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        novos_embeds = [embed]

    try:
        await mensagem_alvo.edit(
            content=mensagem_alvo.content if mensagem_alvo.embeds else None,
            embeds=novos_embeds,
            allowed_mentions=discord.AllowedMentions.none()
        )
        await interaction.followup.send(
            f"✅ Aparência da mensagem de regras atualizada em {canal_alvo.mention}. "
            "Editei a mensagem existente e preservei o texto, os campos e as imagens que já estavam no embed.",
            ephemeral=True
        )
        log.info("Embed de regras atualizado pelo /painel: canal=%s mensagem=%s", canal_alvo.id, message_id)
    except discord.HTTPException as exc:
        log.exception("Falha ao atualizar embed de regras")
        await interaction.followup.send(
            f"❌ Não consegui atualizar o embed de regras ({type(exc).__name__}). Confira os logs do bot.",
            ephemeral=True
        )


class PainelGrupoSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Selecione uma área administrativa...",
            min_values=1,
            max_values=1,
            custom_id="ccbot:painel_grupo",
            options=[
                discord.SelectOption(label="Publicações e produtos", description="Painéis e DJs", emoji="📋", value="publicacoes"),
                discord.SelectOption(label="Central do bot", description="Mensagens, avisos e funcionamento do bot", emoji="🤖", value="bot"),
                discord.SelectOption(label="Personalização", description="Emojis, ícones e imagens", emoji="🎨", value="personalizacao"),
                discord.SelectOption(label="Sistema e backups", description="Cópias de segurança e ferramentas técnicas", emoji="💾", value="sistema"),
                discord.SelectOption(label="Estrutura do servidor", description="Permissões e organização dos canais", emoji="🗂️", value="servidor"),
            ]
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        grupo = self.values[0]
        info = PAINEL_GRUPOS[grupo]
        await interaction.response.edit_message(
            embed=criar_embed_estetico(
                title=info["titulo"],
                description=info["descricao"] + "\n\nSelecione uma função abaixo.",
                color=PAINEL_ADMIN_COLOR
            ),
            view=PainelSubcategoriasView(grupo)
        )

class CategoriaPainelSelect(discord.ui.Select):
    def __init__(self, grupo):
        self.grupo = grupo
        valores = PAINEL_GRUPOS[grupo]["categorias"]
        opcoes = [op for op in OPCOES_CATEGORIAS_PAINEL if op.value in valores]
        super().__init__(
            placeholder="Selecione a função que deseja abrir...",
            min_values=1,
            max_values=1,
            custom_id=f"ccbot:painel_categoria:{grupo}",
            options=opcoes
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return

        categoria = self.values[0]
        if categoria == "djs":
            await interaction.response.edit_message(
                embed=criar_embed_estetico(
                    title="🎧 Gerenciamento de DJs",
                    description="Configure quais DJs aparecem no painel **Gerar Samples**.\n\nUse os botões abaixo para adicionar, alterar ou excluir nomes.",
                    color=discord.Color.green()
                ),
                view=PainelDJsView()
            )
        elif categoria == "boas_vindas":
            await interaction.response.edit_message(
                embed=criar_embed_estetico(
                    title="👋 Boas-vindas",
                    description=f"Canal configurado: <#{WELCOME_CHANNEL_ID}>\n\nUse o botão para publicar uma mensagem de teste nesse canal.",
                    color=discord.Color.green()
                ),
                view=PainelBoasVindasView()
            )
        elif categoria == "regras":
            await atualizar_embed_regras(interaction)
        elif categoria == "parcerias":
            await atualizar_painel_parcerias(interaction)
        elif categoria == "trampar_conosco":
            await atualizar_painel_trampar(interaction)
        elif categoria == "publicar":
            await interaction.response.edit_message(
                embed=criar_embed_estetico(
                    title="📋 Publicar painéis",
                    description=(
                        "Escolha uma função abaixo. Os sistemas continuam ativos; "
                        "esta central apenas reúne os acessos em um só lugar."
                    ),
                    color=discord.Color.green()
                ),
                view=PainelPublicarView()
            )
        elif categoria == "permissoes_categorias":
            await abrir_permissoes_categorias(interaction)
        elif categoria == "trabalhos_on":
            await abrir_trabalhos_on(interaction)
        elif categoria == "analisar_hierarquia":
            await analisar_hierarquia(interaction)
        elif categoria == "organizar_hierarquia":
            await abrir_organizacao_hierarquia(interaction)
        elif categoria == "emojis":
            await interaction.response.edit_message(
                embed=criar_embed_estetico(
                    title="🎨 Personalização • Emojis",
                    description="Envie um ZIP ou imagens para adicionar emojis personalizados ao servidor.",
                    color=PAINEL_ADMIN_COLOR
                ),
                view=PainelEmojisView()
            )
        elif categoria == "icones":
            await interaction.response.edit_message(
                embed=criar_embed_painel_icones(),
                view=PainelIconesView()
            )
        elif categoria == "canal_avisos":
            await interaction.response.edit_message(
                embed=criar_embed_estetico(
                    title="📨 Canal de avisos do bot",
                    description=f"Canal atual: <#{settings.get('notice_channel', DEFAULT_NOTICE_CHANNEL)}>\n\nEscolha abaixo onde o bot deve enviar erros e avisos técnicos.",
                    color=PAINEL_ADMIN_COLOR
                ),
                view=PainelCanalAvisosView()
            )
        elif categoria == "limpar_pv":
            await interaction.response.edit_message(
                embed=criar_embed_estetico(
                    title="🧹 Limpar mensagens do PV",
                    description=(
                        "Esta função apaga **somente mensagens enviadas pelo CC BOT** na conversa privada com você.\n\n"
                        "Suas mensagens não serão apagadas. A limpeza analisa até as **500 mensagens mais recentes** "
                        "e pede confirmação antes de começar."
                    ),
                    color=discord.Color.orange()
                ),
                view=LimparPVView()
            )
        elif categoria == "ferramentas":
            await interaction.response.edit_message(
                embed=criar_embed_estetico(
                    title="🧰 Ferramentas administrativas",
                    description=(
                        "**Emojis:** envie um ZIP ou imagens diretamente pelo formulário do Discord.\n\n"
                        "**Backup:** salva uma cópia no Termux ou envia o ZIP por mensagem privada.\n\n"
                        f"**Canal de avisos atual:** <#{settings.get('notice_channel', DEFAULT_NOTICE_CHANNEL)}>\n"
                        "Escolha o canal no seletor abaixo para alterar onde os erros e avisos são enviados."
                    ),
                    color=discord.Color.green()
                ),
                view=PainelFerramentasView()
            )
        else:
            await interaction.response.edit_message(
                embed=criar_embed_estetico(
                    title="🛠️ Testes e manutenção",
                    description="Use a opção abaixo para conferir se o bot está online e respondendo às interações.",
                    color=discord.Color.green()
                ),
                view=PainelManutencaoView()
            )

class PublicarPainelButton(discord.ui.Button):
    def __init__(self, label, emoji, action):
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.primary,
            custom_id=f"ccbot:publicar_{action}"
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Apenas o administrador pode usar este painel.", ephemeral=True
            )
            return

        # Reutiliza os fluxos originais, sem duplicar a lógica dos sistemas.
        handlers = {
            "emojis": adicionar_emojis,
            "arrumador": arrumador,
            "acapellas": painel_acapellas,
            "samples": samples,
            "beats": venda_de_beats,
            "pack": criar_pack_personalizado,
        }
        await handlers[self.action](interaction)

class PainelPublicarView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(PublicarPainelButton("Enviar ZIP ou imagens", "😀", "emojis"))
        self.add_item(PublicarPainelButton("Arrumador", "🧰", "arrumador"))
        self.add_item(PublicarPainelButton("Acapellas", "🎙️", "acapellas"))
        self.add_item(PublicarPainelButton("Samples", "🎧", "samples"))
        self.add_item(PublicarPainelButton("Venda de beats", "💿", "beats"))
        self.add_item(PublicarPainelButton("Pack personalizado", "📦", "pack"))
        self.add_item(VoltarPainelButton())

class PainelSubcategoriasView(discord.ui.View):
    def __init__(self, grupo):
        super().__init__(timeout=300)
        self.add_item(CategoriaPainelSelect(grupo))
        self.add_item(VoltarPainelButton())

class PainelAdminView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(PainelGrupoSelect())

class PainelDJsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(AdicionarDJButton())
        self.add_item(AlterarDJButton())
        self.add_item(ExcluirDJButton())
        self.add_item(VoltarPainelButton())

class PainelBoasVindasView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(TestarBoasVindasButton())
        self.add_item(VoltarPainelButton())

class VerificarBotButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Verificar bot", emoji="✅", style=discord.ButtonStyle.success, custom_id="ccbot:painel_verificar_bot")

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return

        latency_ms = round(bot.latency * 1000)
        await interaction.response.send_message(
            f"✅ **CC BOT está online.**\nLatência: **{latency_ms} ms**",
            ephemeral=True
        )

class PainelManutencaoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(VerificarBotButton())
        self.add_item(VoltarPainelButton())

def criar_embed_painel():
    return criar_embed_estetico(
        title="CC BOT | Administração",
        description=(
            "Central de controle do servidor.\n\n"
            "Selecione uma área para acessar as ferramentas disponíveis. "
            "As alterações importantes continuam pedindo confirmação antes de serem aplicadas."
        ),
        color=PAINEL_ADMIN_COLOR
    )

class GerarSamplesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Gerar Samples", emoji="<:emoji_1:1561748306818891857>", style=discord.ButtonStyle.success, custom_id="ccbot:gerar_samples")

    async def callback(self, interaction: discord.Interaction):
        if not mcs:
            await interaction.response.send_message(
                "Ainda não há DJs cadastrados.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            view=ListaDJsView(),
            ephemeral=True
        )

class GeradorSamplesView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Container(
                # Banner horizontal em destaque, com descrição acessível.
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(
                        media="https://cdn.discordapp.com/ephemeral-attachments/1442529633919570057/1561721929583566858/605081.jpg?ex=6ad76263&is=6ad610e3&hm=23259b8d57737e9607039f616c91ceea22a3e99d47e000691c14e7115ac69edd&",
                        description="Banner do Gerador de Samples"
                    )
                ),
                discord.ui.Separator(
                    visible=True,
                    spacing=discord.SeparatorSpacing.large
                ),
                discord.ui.Section(
                    discord.ui.TextDisplay(
                        content=(
                            "### 📦  GERADOR DE SAMPLES\n"
                            "**Tenha acesso a novas possibilidades para suas produções!**\n"
                            "-# Novo Gerador de Samples da atualidade\n"
                        )
                    ),
                    accessory=discord.ui.Thumbnail(
                        media="https://cdn.discordapp.com/ephemeral-attachments/1442529633919570057/1561722097011527761/801_Sem_Titulo_20261018095128.png?ex=6ad7628b&is=6ad6110b&hm=5221dbb0652028ae88f6d57d7daa303cb8c86a67373c387f2711e7888011ef69&"
                    )
                ),
                discord.ui.Separator(
                    visible=True,
                    spacing=discord.SeparatorSpacing.large
                ),
                discord.ui.TextDisplay(
                    content=(
                        "## **PAINEL ATUALIZADO**\n"
                        "-# Confira abaixo todas as funcionalidades disponíveis e descubra tudo o que você pode fazer com o nosso painel!\n\n"
                        "### <:box_1:1561761829502459964> Samples No Estilo Dos Djs!\n"
                        "Clique em **Gerar Samples** e escolha um DJ cadastrado pelo administrador.\n\n"
                        "### <:box_1:1561761829502459964> Gere __Quantas Samples Quiser!__\n"
                        "Tenha liberdade para gerar **quantas samples quiser**, de forma prática, rápida e direto pelo painel."
                    )
                ),
                discord.ui.ActionRow(GerarSamplesButton())
            )
        )

# Importação de emojis pelo formulário do Discord: ZIP ou imagens soltas.
EMOJI_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
EMOJI_UPLOAD_DIR = BASE / "uploads" / "emojis"
EMOJI_ZIP_MAX_BYTES = 100 * 1024 * 1024
EMOJI_ZIP_MAX_UNPACKED = 100 * 1024 * 1024
EMOJI_ZIP_MAX_IMAGE = 20 * 1024 * 1024
EMOJI_ZIP_MAX_IMAGES = 500

def nome_seguro_emoji(filename):
    nome = Path(filename).stem.casefold().replace("-", "_").replace(" ", "_")
    nome = re.sub(
        r"[^a-z0-9_]",
        "_",
        unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    )
    nome = re.sub(r"_+", "_", nome).strip("_")
    return (nome if len(nome) >= 2 else f"em_{nome or 'emoji'}")[:32]

class EmojiUploadModal(discord.ui.Modal, title="Enviar emojis e GIFs"):
    arquivos = discord.ui.Label(
        text="ZIP ou imagens (até 10 arquivos)",
        description="Anexe ZIPs ou imagens PNG, JPG, WEBP e GIF. Pode misturar os tipos.",
        component=discord.ui.FileUpload(
            custom_id="ccbot_emojis_files",
            min_values=1,
            max_values=10,
            required=True
        )
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode importar emojis.", ephemeral=True)
            return
        if interaction.guild is None:
            await interaction.response.send_message("Use este formulário dentro do servidor.", ephemeral=True)
            return
        guild = interaction.guild
        membro = guild.me
        if membro is None or not membro.guild_permissions.manage_emojis_and_stickers:
            await interaction.response.send_message(
                "O bot precisa da permissão Gerenciar Expressões neste servidor.",
                ephemeral=True
            )
            return

        anexos = self.arquivos.component.values
        await interaction.response.defer(ephemeral=True, thinking=True)
        pasta_lote = EMOJI_UPLOAD_DIR / f"{interaction.user.id}_{uuid.uuid4().hex}"
        pasta_imagens = pasta_lote / "images"
        pasta_imagens.mkdir(parents=True, exist_ok=True)
        imagens = []
        total_recebido = 0
        total_descompactado = 0

        try:
            for anexo in anexos:
                extensao = Path(anexo.filename).suffix.lower()
                if extensao != ".zip" and extensao not in EMOJI_EXTENSIONS:
                    continue
                if anexo.size > EMOJI_ZIP_MAX_BYTES:
                    raise ValueError(f"{Path(anexo.filename).name} ultrapassa 100 MB.")
                dados = await anexo.read(use_cached=True)
                total_recebido += len(dados)
                if total_recebido > EMOJI_ZIP_MAX_BYTES:
                    raise ValueError("O total enviado ultrapassa 100 MB.")
                nome_original = Path(anexo.filename).name.replace("/", "_").replace(chr(92), "_")

                if extensao == ".zip":
                    (pasta_lote / f"original_{nome_original}").write_bytes(dados)
                    with zipfile.ZipFile(io.BytesIO(dados), "r") as arquivo_zip:
                        membros_zip = sorted(arquivo_zip.infolist(), key=lambda item: item.filename.casefold())
                        candidatos = [
                            item for item in membros_zip
                            if not item.is_dir()
                            and Path(item.filename.replace(chr(92), "/")).suffix.lower() in EMOJI_EXTENSIONS
                            and not Path(item.filename.replace(chr(92), "/")).name.startswith(".")
                        ]
                        if len(imagens) + len(candidatos) > EMOJI_ZIP_MAX_IMAGES:
                            raise ValueError("O limite é de 500 imagens por envio.")
                        for item in candidatos:
                            nome_arquivo = Path(item.filename.replace(chr(92), "/")).name
                            if item.file_size <= 0:
                                continue
                            if item.file_size > EMOJI_ZIP_MAX_IMAGE:
                                raise ValueError(f"{nome_arquivo} ultrapassa 20 MB descompactado.")
                            total_descompactado += item.file_size
                            if total_descompactado > EMOJI_ZIP_MAX_UNPACKED:
                                raise ValueError("As imagens descompactadas ultrapassam 100 MB no total.")
                            conteudo = arquivo_zip.read(item)
                            if not conteudo:
                                continue
                            destino = pasta_imagens / f"{len(imagens) + 1:04d}_{nome_arquivo}"
                            destino.write_bytes(conteudo)
                            imagens.append({"path": str(destino), "name": nome_seguro_emoji(nome_arquivo), "size": len(conteudo)})
                else:
                    if len(imagens) >= EMOJI_ZIP_MAX_IMAGES:
                        raise ValueError("O limite é de 500 imagens por envio.")
                    if len(dados) > EMOJI_ZIP_MAX_IMAGE:
                        raise ValueError(f"{nome_original} ultrapassa 20 MB.")
                    destino = pasta_imagens / f"{len(imagens) + 1:04d}_{nome_original}"
                    destino.write_bytes(dados)
                    imagens.append({"path": str(destino), "name": nome_seguro_emoji(nome_original), "size": len(dados)})

            if not imagens:
                raise ValueError("Envie um ZIP com imagens ou imagens soltas PNG, JPG, WEBP ou GIF.")

            existentes = {emoji.name.casefold() for emoji in guild.emojis}
            adicionados, ignorados, falhas = [], [], []
            for registro in imagens:
                nome = registro["name"]
                if nome.casefold() in existentes:
                    ignorados.append(f":{nome}:")
                    continue
                try:
                    conteudo = await asyncio.to_thread(Path(registro["path"]).read_bytes)
                    emoji = await guild.create_custom_emoji(
                        name=nome,
                        image=conteudo,
                        reason=f"Importação por formulário do Discord pelo administrador {interaction.user.id}"
                    )
                    adicionados.append(f"{emoji} :{nome}:")
                    existentes.add(nome.casefold())
                except discord.Forbidden:
                    falhas.append(f"{nome}: sem permissão ou limite de emojis do servidor")
                    break
                except discord.HTTPException as exc:
                    falhas.append(f"{nome}: Discord HTTP {exc.status} — {str(exc)[:80]}")
                except Exception as exc:
                    log.exception("Falha ao importar emoji do formulário: %s", nome)
                    falhas.append(f"{nome}: {type(exc).__name__}")

            resumo = (
                "## Importação de emojis concluída\n"
                f"Imagens no envio: {len(imagens)}\n"
                f"Adicionados: {len(adicionados)} • Já existentes: {len(ignorados)} • Falhas: {len(falhas)}\n"
                f"Arquivos salvos em: {pasta_lote}"
            )
            detalhes = []
            if adicionados:
                detalhes.append("Adicionados: " + ", ".join(adicionados[:20]))
            if ignorados:
                detalhes.append("Já existiam: " + ", ".join(ignorados[:10]))
            if falhas:
                detalhes.append("Falhas: " + "\n".join(falhas[:10]))
            await interaction.followup.send(
                resumo + ("\n\n" + "\n".join(detalhes) if detalhes else ""),
                ephemeral=True
            )
        except (zipfile.BadZipFile, OSError, ValueError) as exc:
            log.warning("Upload de emojis inválido: %s", exc)
            await interaction.followup.send(f"Não consegui processar os arquivos: {str(exc)[:350]}", ephemeral=True)
        except Exception as exc:
            log.exception("Falha no formulário de importação de emojis")
            await interaction.followup.send(f"Falha ao importar ({type(exc).__name__}).", ephemeral=True)

async def adicionar_emojis(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador pode importar emojis.", ephemeral=True
        )
        return
    if interaction.guild is None:
        await interaction.response.send_message("Use esta função dentro do servidor.", ephemeral=True)
        return
    await interaction.response.send_modal(EmojiUploadModal())

async def samples(interaction: discord.Interaction):
    if await deny_unauthorized(interaction):
        return
    await interaction.response.send_message(view=GeradorSamplesView())

class AcapellasActivityButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="ENTRAR",
            emoji="🔎",
            style=discord.ButtonStyle.primary,
            custom_id="ccbot:acapellas_launch_activity"
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            # Abre a Activity configurada no Developer Portal dentro do Discord.
            await interaction.response.launch_activity()
        except discord.HTTPException:
            log.exception("Falha ao lançar Activity de acapellas")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Não consegui abrir a Activity. Confira se Atividades está ativado e se o mapeamento de URL está válido.",
                    ephemeral=True
                )

class AcapellasActivityView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(AcapellasActivityButton())

async def painel_acapellas(interaction: discord.Interaction):
    if await deny_unauthorized(interaction):
        return

    canal_id = 1561794460617277511
    try:
        canal = bot.get_channel(canal_id)
        if canal is None:
            canal = await bot.fetch_channel(canal_id)
        if not isinstance(canal, discord.TextChannel):
            await interaction.response.send_message(
                "O canal configurado não é um canal de texto.", ephemeral=True
            )
            return

        embed = criar_embed_estetico(
            title="🎙️ ACAPELLAS DE MCs FAMOSOS",
            description=(
                "**Problemas em achar acapellas de MCs famosos?**\n"
                "Seus problemas acabaram! 🔥\n\n"
                "Clique no botão abaixo e procure **acapellas de graça**."
            ),
            color=discord.Color.red()
        )
        embed.set_footer(text="CC BOT • Busca de Acapellas")

        painel_existente = None
        async for mensagem in canal.history(limit=100):
            if (
                mensagem.author.id == bot.user.id
                and mensagem.embeds
                and mensagem.embeds[0].title == "🎙️ ACAPELLAS DE MCs FAMOSOS"
            ):
                painel_existente = mensagem
                break

        if painel_existente:
            await painel_existente.edit(embed=embed, view=AcapellasActivityView())
            resultado = f"✅ Painel existente atualizado em {canal.mention}."
        else:
            await canal.send(embed=embed, view=AcapellasActivityView())
            resultado = f"✅ Painel de acapellas publicado em {canal.mention}."

        await interaction.response.send_message(resultado, ephemeral=True)
    except Exception as exc:
        log.exception("Falha ao publicar painel de acapellas")
        if interaction.response.is_done():
            await interaction.followup.send(
                "❌ Não consegui publicar o painel. Confira as permissões do bot no canal.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ Não consegui publicar o painel. Confira as permissões do bot no canal.",
                ephemeral=True
            )

class ArrumadorModal(discord.ui.Modal, title="Arrumador de Projetos"):
    mudancas = discord.ui.TextInput(
        label="O que você quer mudar no projeto?",
        placeholder="Ex.: organizar pastas, renomear arquivos, separar samples...",
        style=discord.TextStyle.paragraph,
        min_length=3,
        max_length=1500
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            ticket, txid = await criar_pedido_pagamento(
                interaction,
                "Arrumador de Projetos",
                33.99,
                f"Pedido de mudança: {str(self.mudancas.value).strip()}",
                tipo="arrumador"
            )
            await interaction.edit_original_response(
                content=(
                    f"🧾 **Arrumador de Projetos — {formatar_reais(33.99)}**\n"
                    f"Seu pedido foi registrado. Faça o Pix no canal privado {ticket.mention}.\n"
                    f"Identificador: {txid}.\n"
                    "Depois do pagamento, aguarde a confirmação manual. O envio do ZIP será liberado após a aprovação."
                ),
                view=None
            )
        except Exception as exc:
            log.exception("Falha ao abrir checkout Pix do Arrumador")
            await interaction.edit_original_response(
                content=f"Não consegui abrir o pagamento: {str(exc)[:400]}",
                view=None
            )
            return

        # Só solicita e encaminha os arquivos depois da aprovação manual do Pix.
        prazo_pagamento = asyncio.get_running_loop().time() + 1800
        while asyncio.get_running_loop().time() < prazo_pagamento:
            pedido_pagamento = pix_orders.get(txid, {})
            status_pagamento = pedido_pagamento.get("status")
            if status_pagamento == "pago_confirmado_manualmente":
                break
            if status_pagamento == "cancelado_manualmente":
                return
            await asyncio.sleep(5)
        else:
            try:
                await interaction.user.send(
                    f"⏰ O prazo de 30 minutos para confirmar o Pix do Arrumador expirou. "
                    f"Se você já pagou, fale com a administração e informe o identificador {txid}."
                )
            except discord.HTTPException:
                pass
            return

        try:
            await interaction.user.send(
                "**🧰 Arrumador de Projetos — pagamento confirmado**\n"
                "Agora envie o ZIP aqui no PV se o Discord permitir.\n\n"
                "**Se o arquivo for grande e o Discord pedir Nitro:**\n"
                "1. Envie o ZIP para o Google Drive.\n"
                "2. No Drive, abra **Compartilhar** e mude o acesso para **Qualquer pessoa com o link** (leitor).\n"
                "3. Cole o link aqui nesta conversa privada.\n\n"
                "Aceito anexos ZIP ou links do Google Drive, Dropbox, OneDrive e WeTransfer. Você tem **10 minutos**."
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Não consegui abrir seu PV. Ative mensagens diretas deste servidor e fale com a administração para continuar após o pagamento.",
                ephemeral=True
            )
            return

        hosts_permitidos = (
            "drive.google.com", "docs.google.com", "dropbox.com", "www.dropbox.com",
            "1drv.ms", "onedrive.live.com", "wetransfer.com", "www.wetransfer.com"
        )

        def mensagem_do_usuario(message: discord.Message):
            return (
                message.author.id == interaction.user.id
                and isinstance(message.channel, discord.DMChannel)
                and (bool(message.attachments) or bool(message.content.strip()))
            )

        limite = asyncio.get_running_loop().time() + 600
        while True:
            restante = limite - asyncio.get_running_loop().time()
            if restante <= 0:
                try:
                    await interaction.user.send(
                        "⏰ O prazo do Arrumador expirou. Execute /arrumador novamente para iniciar outro pedido."
                    )
                except discord.HTTPException:
                    pass
                return

            try:
                mensagem_zip = await bot.wait_for(
                    "message", check=mensagem_do_usuario, timeout=restante
                )
            except asyncio.TimeoutError:
                try:
                    await interaction.user.send(
                        "⏰ O prazo do Arrumador expirou. Execute /arrumador novamente para iniciar outro pedido."
                    )
                except discord.HTTPException:
                    pass
                return

            anexo = next(
                (a for a in mensagem_zip.attachments if Path(a.filename).suffix.lower() == ".zip"),
                None
            )

            if anexo is None:
                texto = mensagem_zip.content.strip()
                urls = [parte.strip("<>") for parte in texto.split() if parte.startswith(("https://", "http://"))]
                url_valida = None
                for url in urls:
                    parsed = urlparse(url)
                    host = (parsed.hostname or "").lower()
                    if parsed.scheme == "https" and any(
                        host == permitido or host.endswith("." + permitido)
                        for permitido in hosts_permitidos
                    ):
                        url_valida = url
                        break

                if url_valida:
                    resumo = (
                        "🧰 **Novo pedido — Arrumador de Projetos**\n"
                        f"**Pessoa:** {interaction.user} (`{interaction.user.id}`)\n"
                        f"**Pedido de mudança:**\n{self.mudancas.value}\n"
                        "**Arquivo:** enviado por link externo (não anexado ao Discord)\n"
                        f"**Link do arquivo:** {url_valida}\n"
                        "⚠️ Confira se o link está acessível; o bot não consegue validar o conteúdo do arquivo hospedado."
                    )
                    erros = []
                    try:
                        admin = bot.get_user(ADMIN_ID) or await bot.fetch_user(ADMIN_ID)
                        await admin.send(resumo)
                    except Exception:
                        erros.append("PV do administrador")
                        log.exception("Falha ao enviar link do Arrumador por PV.")

                    try:
                        canal_id = int(settings.get("notice_channel", DEFAULT_NOTICE_CHANNEL))
                        canal = bot.get_channel(canal_id)
                        if canal is None:
                            canal = await bot.fetch_channel(canal_id)
                        await canal.send(resumo)
                    except Exception:
                        erros.append("canal de avisos")
                        log.exception("Falha ao enviar link do Arrumador ao canal de avisos.")

                    if len(erros) == 2:
                        await interaction.user.send(
                            "❌ Não consegui encaminhar seu pedido. Avise o administrador e tente novamente mais tarde."
                        )
                    else:
                        await interaction.user.send(
                            "✅ Seu pedido e o link do arquivo foram encaminhados para análise."
                            + (f"\n⚠️ O envio falhou em: {', '.join(erros)}." if erros else "")
                        )
                    return

                await interaction.user.send(
                    "⚠️ Não encontrei um anexo .ZIP nem um link compatível. Envie o ZIP ou um link HTTPS do Google Drive, Dropbox, OneDrive ou WeTransfer."
                )
                continue

            if anexo.size > 24 * 1024 * 1024:
                await interaction.user.send(
                    "⚠️ Esse anexo ultrapassa o limite deste fluxo. Para não precisar de Nitro, envie o ZIP ao Google Drive, habilite **Qualquer pessoa com o link** e cole o link aqui."
                )
                continue

            try:
                dados = await anexo.read(use_cached=True)
                with zipfile.ZipFile(io.BytesIO(dados), "r") as arquivo:
                    problema = arquivo.testzip()
                if problema is not None:
                    raise zipfile.BadZipFile("O arquivo contém um item corrompido.")
            except (discord.HTTPException, zipfile.BadZipFile, OSError, ValueError):
                await interaction.user.send("⚠️ Não consegui validar esse ZIP. Confira se ele abre normalmente e envie novamente.")
                continue
            except Exception:
                log.exception("Erro ao receber o ZIP do Arrumador.")
                await interaction.user.send("⚠️ Ocorreu um erro ao verificar o ZIP. Tente enviar novamente.")
                continue

            nome_seguro = Path(anexo.filename).name.replace("/", "_").replace(chr(92), "_")
            resumo = (
                "🧰 **Novo pedido — Arrumador de Projetos**\n"
                f"**Pessoa:** {interaction.user} (`{interaction.user.id}`)\n"
                f"**Pedido de mudança:**\n{self.mudancas.value}\n"
                f"**Arquivo:** `{nome_seguro}`"
            )
            erros = []
            try:
                admin = bot.get_user(ADMIN_ID) or await bot.fetch_user(ADMIN_ID)
                await admin.send(
                    resumo,
                    file=discord.File(io.BytesIO(dados), filename=nome_seguro)
                )
            except Exception:
                erros.append("PV do administrador")
                log.exception("Falha ao enviar pedido do Arrumador por PV.")

            try:
                canal_id = int(settings.get("notice_channel", DEFAULT_NOTICE_CHANNEL))
                canal = bot.get_channel(canal_id)
                if canal is None:
                    canal = await bot.fetch_channel(canal_id)
                await canal.send(
                    resumo,
                    file=discord.File(io.BytesIO(dados), filename=nome_seguro)
                )
            except Exception:
                erros.append("canal de avisos")
                log.exception("Falha ao enviar pedido do Arrumador ao canal de avisos.")

            if len(erros) == 2:
                await interaction.user.send(
                    "❌ Não consegui encaminhar seu pedido. Avise o administrador e tente novamente mais tarde."
                )
                log.error("Pedido Arrumador não foi entregue a nenhum destino.")
                return

            await interaction.user.send(
                "✅ Seu pedido e o ZIP foram encaminhados para análise."
                + (f"\n⚠️ O envio falhou em: {', '.join(erros)}." if erros else "")
            )
            return

class MandarArrumadorButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="MANDAR",
            emoji="📦",
            style=discord.ButtonStyle.success,
            custom_id="ccbot:arrumador_mandar"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ArrumadorModal())

class ComprovanteInfoView(discord.ui.View):
    """View sem botões para o painel informativo de comprovantes."""
    def __init__(self):
        super().__init__(timeout=None)


def criar_embed_comprovante_info():
    embed = criar_embed_estetico(
        title="🧾 COMO ENVIAR O COMPROVANTE",
        description=(
            "Envie o comprovante dentro do **canal privado do seu pedido**.\n\n"
            "### 📎 PASSO A PASSO\n"
            "1. Abra o pedido em andamento.\n"
            "2. Use **/comprovante** e selecione a imagem ou o PDF.\n"
            "3. Aguarde a conferência manual do administrador.\n\n"
            "### ⚠️ IMPORTANTE\n"
            "Enviar o arquivo não confirma o pagamento. O pedido só é liberado após a verificação do Pix.\n"
            "Formatos aceitos: PNG, JPG, WEBP ou PDF · até 10 MB."
        ),
        color=discord.Color.from_rgb(40, 40, 40)
    )
    embed.set_footer(text="CC BOT • Pagamentos conferidos manualmente")
    return embed


class ArrumadorPainelView(discord.ui.LayoutView):
    """Painel Arrumador em Components V2, sem embeds tradicionais nem imagens expiradas."""
    def __init__(self):
        super().__init__(timeout=None)
        mandar = MandarArrumadorButton()
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(
                content=(
                    "# 🧰 ARRUMADOR DE PROJETOS\n"
                    "-# FL STUDIO MOBILE · ORGANIZAÇÃO PERSONALIZADA\n\n"
                    "Seu projeto, organizado conforme as alterações que você precisa."
                )
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                content=(
                    "### SERVIÇO\n"
                    "## R$ 33,99\n"
                    "-# Pagamento via Pix · confirmação manual"
                )
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                content=(
                    "### COMO SOLICITAR\n"
                    "**01**  Toque em **MANDAR** e descreva o que precisa mudar.\n\n"
                    "**02**  Abra seu pedido privado e faça o Pix.\n\n"
                    "**03**  Envie o comprovante e aguarde a conferência do ADM.\n\n"
                    "-# O envio do projeto para organização é liberado após a confirmação do pagamento."
                )
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.ActionRow(mandar)
        ))

def criar_embed_arrumador():
    """Mantém a chamada antiga compatível, retornando o painel Components V2."""
    return ArrumadorPainelView()

async def arrumador(interaction: discord.Interaction):
    """Atualiza a mensagem original do Arrumador; nunca cria uma cópia."""
    if await deny_unauthorized(interaction):
        return

    canal_id = 1561513289438199878
    mensagem_id = 1563997360432873624
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        canal = bot.get_channel(canal_id)
        if canal is None:
            canal = await bot.fetch_channel(canal_id)
        mensagem = await canal.fetch_message(mensagem_id)
        if bot.user is None or mensagem.author.id != bot.user.id:
            raise PermissionError("A mensagem configurada não pertence ao CC BOT.")

        # Ao trocar para Components V2, limpa os campos legados.
        # Não passar embed e embeds juntos: discord.py os considera argumentos conflitantes.
        await mensagem.edit(
            content=None,
            embeds=[],
            attachments=[],
            view=criar_embed_arrumador(),
            allowed_mentions=discord.AllowedMentions.none()
        )
        await interaction.followup.send(
            f"✅ Aba do Arrumador atualizada na mensagem existente em {canal.mention}. Nenhuma cópia foi criada.",
            ephemeral=True
        )
        log.info("Painel do Arrumador atualizado: canal=%s mensagem=%s", canal_id, mensagem_id)
    except discord.NotFound:
        await interaction.followup.send(
            "❌ Não encontrei a mensagem configurada do Arrumador. Não criei outra mensagem.",
            ephemeral=True
        )
    except discord.Forbidden:
        log.exception("Sem permissão para atualizar o painel do Arrumador.")
        await interaction.followup.send(
            "❌ O bot não tem permissão para editar a mensagem do Arrumador.",
            ephemeral=True
        )
    except PermissionError as exc:
        log.error("Painel Arrumador protegido: %s", exc)
        await interaction.followup.send(
            "❌ A mensagem configurada não pertence ao CC BOT. Nenhuma mensagem foi alterada ou criada.",
            ephemeral=True
        )
    except discord.HTTPException as exc:
        log.exception("Falha ao atualizar o painel do Arrumador.")
        await interaction.followup.send(
            f"❌ Não consegui atualizar o painel ({type(exc).__name__}, código {getattr(exc, 'code', 'n/d')}). Confira os logs do bot.",
            ephemeral=True
        )

PROMO_CHANNELS = {
    "Loja CC": 1561513281049595958,
    "PACKy": 1561513282265944104,
    "Garimpo": 1561513283171651644,
}
PROMO_URLS = {
    "Loja CC": "https://ccloja.erbysite.com/",
    "PACKy": "https://packy.lovable.app",
    "Garimpo": "https://garimoh.lovable.app/perfil",
}

class PromoLinksView(discord.ui.View):
    def __init__(self, label: str, url: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.link,
            url=url,
            emoji="🔗"
        ))

def criar_embed_promocional(nome: str) -> discord.Embed:
    textos = {
        "Loja CC": "Confira os produtos e opções disponíveis na Loja CC.",
        "PACKy": "Explore packs e conteúdos para sua produção musical.",
        "Garimpo": "Descubra novidades e conteúdos disponíveis no Garimpo.",
    }
    embed = criar_embed_estetico(
        title="Acesse",
        description=f"**{nome}**\n\n{textos[nome]}",
        color=discord.Color.from_rgb(0, 0, 0)
    )
    guild = bot.get_guild(1561513043886743562)
    if guild and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="CC • LINKS OFICIAIS")
    return embed

async def publicar_embeds_promocionais():
    """Atualiza os embeds já publicados; nunca cria mensagens promocionais novas."""
    publicados = settings.get("promo_embeds", {})
    for nome, canal_id in PROMO_CHANNELS.items():
        registro = publicados.get(nome, {})
        mensagem_id = registro.get("message_id") if isinstance(registro, dict) else None
        if not mensagem_id:
            log.warning(
                "Embed promocional de %s não foi alterado: ID da mensagem não está salvo.",
                nome
            )
            continue
        try:
            canal = bot.get_channel(canal_id)
            if canal is None:
                canal = await bot.fetch_channel(canal_id)
            mensagem = await canal.fetch_message(int(mensagem_id))
            await mensagem.edit(
                embed=criar_embed_promocional(nome),
                view=PromoLinksView("Acesse", PROMO_URLS[nome])
            )
            log.info(
                "Embed promocional atualizado: %s no canal %s (mensagem %s)",
                nome, canal_id, mensagem.id
            )
        except Exception:
            log.exception("Falha ao atualizar embed promocional: %s", nome)

PromoLinksView, criar_embed_promocional, publicar_embeds_promocionais = configure_promos(
    bot, PROMO_CHANNELS, PROMO_URLS, settings, DATA_FILE, save_json, log
)

# Painéis de Parcerias e Trampar Conosco — publicados/atualizados sem duplicar mensagens.
PARTNERSHIPS_CHANNEL_ID = 1561843704518549604
WORK_WITH_US_CHANNEL_ID = 1561843767860789318
WHATSAPP_NUMBER = "5511914038635"
CONTACT_DISCORD_ID = ADMIN_ID
SupportPanelView, TicketCloseView, criar_embed_suporte, garantir_painel_suporte, garantir_regras, garantir_canal_divulgacoes = configure_support(bot, ADMIN_ID, WHATSAPP_NUMBER, is_admin, log)

def link_whatsapp(mensagem: str) -> str:
    from urllib.parse import quote
    return f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(mensagem)}"

class ContatoOportunidadeView(discord.ui.View):
    def __init__(self, assunto: str):
        super().__init__(timeout=300)
        texto = (
            "Olá! Quero fechar uma parceria com o servidor C.C ©. "
            "Já tenho os requisitos mínimos e gostaria de conversar."
            if assunto == "Parceria"
            else f"Olá! Tenho interesse em trampar com o C.C © na área de {assunto}. Gostaria de conversar."
        )
        self.add_item(discord.ui.Button(
            label="Entrar em contato pelo Discord",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/users/{CONTACT_DISCORD_ID}",
            emoji="💬"
        ))
        self.add_item(discord.ui.Button(
            label="Entrar em contato pelo WhatsApp",
            style=discord.ButtonStyle.link,
            url=link_whatsapp(texto),
            emoji="📱"
        ))

class SelecionarAreaTrabalho(discord.ui.Select):
    def __init__(self):
        opcoes = [
            discord.SelectOption(label="Bots para Discord", value="Bots para Discord", emoji="🤖"),
            discord.SelectOption(label="Design", value="Design", emoji="🎨"),
            discord.SelectOption(label="Organização de servidor / Staff", value="Organização de servidor / Staff", emoji="🛠️"),
            discord.SelectOption(label="Edição de vídeo", value="Edição de vídeo", emoji="🎬"),
        ]
        super().__init__(
            placeholder="Selecione a área em que você trabalha",
            min_values=1,
            max_values=1,
            options=opcoes,
            custom_id="ccbot:trabalhar:area"
        )

    async def callback(self, interaction: discord.Interaction):
        area = self.values[0]
        embed = criar_embed_estetico(
            title=f"💼 Interesse em trabalhar — {area}",
            description=(
                f"Você selecionou **{area}**. Escolha como quer entrar em contato "
                "com o responsável pelo C.C ©."
            ),
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(
            embed=embed,
            view=ContatoOportunidadeView(area),
            ephemeral=True
        )

class SelecionarAreaTrabalhoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(SelecionarAreaTrabalho())

class AbrirAreasTrabalhoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Demonstrar interesse",
            emoji="✦",
            style=discord.ButtonStyle.primary,
            custom_id="ccbot:trabalhar:abrir"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Qual especialidade você gostaria de apresentar?",
            view=SelecionarAreaTrabalhoView(),
            ephemeral=True
        )

class TramparConoscoView(discord.ui.LayoutView):
    """Central de oportunidades com identidade textual própria, sem emojis."""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    content=(
                        "# ✦ CENTRAL DE OPORTUNIDADES\n"
                        "## Transforme habilidade em projeto.\n"
                        "Um espaço para pessoas que querem contribuir, desenvolver ideias e apresentar o próprio trabalho à comunidade."
                    )
                ),
                discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(
                    content=(
                        "### ESPECIALIDADES\n"
                        "⚙️ **Desenvolvimento**\n"
                        "Bots para Discord, ferramentas em Python, automações e soluções com ou sem inteligência artificial.\n\n"
                        "🎨 **Direção visual**\n"
                        "Banners, artes para comunidades, identidade visual e materiais de divulgação.\n\n"
                        "🛡️ **Comunidade e suporte**\n"
                        "Organização de canais, atendimento, moderação e estruturação de servidores.\n\n"
                        "🎬 **Produção audiovisual**\n"
                        "Edição de vídeos. Esta área poderá ser aberta futuramente."
                    )
                ),
                discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
                discord.ui.Section(
                    discord.ui.TextDisplay(
                        content=(
                            "### 📌 COMO PARTICIPAR\n"
                            "Selecione sua especialidade no botão ao lado e informe em que pode contribuir. "
                            "Isso registra seu interesse, mas não representa aprovação automática."
                        )
                    ),
                    accessory=AbrirAreasTrabalhoButton()
                ),
                discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(
                    content=(
                        "### ⏳ STATUS DO PROGRAMA\n"
                        "As inscrições oficiais estão fechadas no momento. As manifestações de interesse podem ser usadas para organizar futuras oportunidades.\n\n"
                        "-# C.C ©  /  DESENVOLVIMENTO • CRIATIVIDADE • COMUNIDADE"
                    )
                ),
                accent_color=5793266
            )
        )

class ParceriasView(discord.ui.LayoutView):
    """Painel de parcerias simples, direto e organizado."""
    def __init__(self):
        super().__init__(timeout=None)
        botao_parceria = discord.ui.Button(
            label="Propor parceria",
            style=discord.ButtonStyle.link,
            url=link_whatsapp(
                "Olá! Quero propor uma parceria com o C.C ©. "
                "Meu grupo tem pelo menos 30 membros. Como podemos alinhar a parceria?"
            ),
            emoji="🤝"
        )
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(content=(
                "# 🤝 PARCERIAS C.C ©\n"
                "### Vamos crescer juntos.\n"
                "Tem uma comunidade ativa? Apresente seu grupo e converse com a administração sobre uma parceria."
            )),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=(
                "### 📌 REQUISITOS\n"
                "• Grupo de WhatsApp com **30 membros ou mais**\n"
                "• Conceder ao responsável do C.C © **permissão de administrador** no grupo"
            )),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=(
                "### ✨ O QUE PODEMOS FAZER\n"
                "Divulgação entre comunidades e, quando combinado, um espaço no servidor para apresentar projetos e serviços."
            )),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.ActionRow(botao_parceria),
            accent_color=5793266
        ))
        self.add_item(discord.ui.TextDisplay(
            content="-# C.C ©  •  PARCERIAS E COMUNIDADE"
        ))


def criar_embed_trampar_conosco() -> discord.Embed:
    embed = criar_embed_estetico(
        title="🏷️ TRAMPAR CONOSCO",
        description=(
            "**Seu talento tem espaço no C.C ©.**\n"
            "-# PAINEL ATUALIZADO\n\n"
            "Escolha uma área no botão abaixo para conhecer o processo."
        ),
        color=discord.Color.from_rgb(0, 0, 0)
    )
    embed.add_field(
        name="📦 ÁREAS",
        value=(
            "🤖 **Bots para Discord** — Python, com ou sem IA\n"
            "🎨 **Design** — banners, artes e identidade visual\n"
            "🛠️ **Organização / Staff** — canais e moderação\n"
            "🎬 **Edição de vídeo** — planejada para o futuro"
        ),
        inline=False
    )
    embed.add_field(
        name="🎁 O QUE VOCÊ GANHA",
        value="Um espaço no servidor para divulgar seu trabalho e seus projetos.",
        inline=False
    )
    embed.add_field(
        name="⏳ INSCRIÇÕES",
        value="As inscrições oficiais ainda estão fechadas; o botão permite indicar sua área de interesse.",
        inline=False
    )
    embed.set_footer(text="C.C © • Escolha uma área no botão abaixo")
    return embed

async def atualizar_painel_parcerias(interaction: discord.Interaction):
    """Atualiza exclusivamente a mensagem de Parcerias identificada pelo administrador."""
    if not is_admin(interaction):
        await interaction.response.send_message("Apenas o administrador pode usar esta função.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        canal = bot.get_channel(PARTNERSHIPS_CHANNEL_ID)
        if canal is None:
            canal = await bot.fetch_channel(PARTNERSHIPS_CHANNEL_ID)
        if not isinstance(canal, discord.TextChannel):
            await interaction.edit_original_response(
                content="❌ O canal de Parcerias não foi encontrado ou não é um canal de texto.",
                embed=None, view=None
            )
            return

        # Atualiza a mensagem existente informada pelo administrador; nunca cria duplicatas.
        mensagem_id = 1562596483688300545
        registros = settings.setdefault("community_panels", {})
        registro = registros.get("Parcerias", {})
        if isinstance(registro, dict) and registro.get("message_id"):
            mensagem_id = int(registro["message_id"])

        try:
            mensagem = await canal.fetch_message(mensagem_id)
        except (discord.NotFound, discord.HTTPException, ValueError, TypeError):
            await interaction.edit_original_response(
                content=f"❌ Não encontrei a mensagem de Parcerias {mensagem_id} no canal <#{PARTNERSHIPS_CHANNEL_ID}>. Nenhuma mensagem nova foi criada.",
                embed=None, view=None
            )
            return

        await mensagem.edit(
            content=None,
            embeds=[],
            attachments=[],
            view=ParceriasView()
        )
        registros["Parcerias"] = {"message_id": mensagem.id}
        save_json(DATA_FILE, settings)
        await interaction.edit_original_response(
            content=f"✅ Painel **Parcerias atualizado** em <#{PARTNERSHIPS_CHANNEL_ID}>. Mensagem existente reutilizada; nenhuma duplicata criada.",
            embed=None,
            view=None
        )
        log.info("Painel Parcerias atualizado pelo /painel (%s), mensagem %s", interaction.user.id, mensagem.id)
    except Exception as exc:
        log.exception("Falha ao atualizar o painel Parcerias pelo /painel")
        detalhe = f" (HTTP {exc.status}, código {exc.code})" if isinstance(exc, discord.HTTPException) else ""
        if isinstance(exc, discord.HTTPException) and getattr(exc, "text", None):
            log.error("Resposta HTTP do Discord ao atualizar painel de Parcerias: %s", exc.text[:1000])
        try:
            await interaction.edit_original_response(
                content=f"❌ Não consegui atualizar o painel ({type(exc).__name__}{detalhe}). Confira os logs do bot.",
                embeds=[],
                view=None
            )
        except discord.HTTPException:
            log.exception("Também falhou ao enviar o diagnóstico da atualização de Parcerias")


async def atualizar_painel_trampar(interaction: discord.Interaction):
    """Atualiza somente o painel Trampar Conosco, sem tocar em outros canais/painéis."""
    if not is_admin(interaction):
        await interaction.response.send_message("Apenas o administrador pode usar esta função.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        canal = bot.get_channel(WORK_WITH_US_CHANNEL_ID)
        if canal is None:
            canal = await bot.fetch_channel(WORK_WITH_US_CHANNEL_ID)
        if not isinstance(canal, discord.TextChannel):
            await interaction.edit_original_response(content="❌ O canal Trampar Conosco não foi encontrado ou não é um canal de texto.", embed=None, view=None)
            return

        registros = settings.setdefault("community_panels", {})
        registro = registros.get("Trampar Conosco", {})
        mensagem = None
        mensagem_id = registro.get("message_id") if isinstance(registro, dict) else None
        if mensagem_id:
            try:
                mensagem = await canal.fetch_message(int(mensagem_id))
            except (discord.NotFound, discord.HTTPException, ValueError, TypeError):
                mensagem = None

        if mensagem is None:
            async for candidata in canal.history(limit=100):
                if candidata.author.id != bot.user.id or not candidata.embeds:
                    continue
                titulo = (candidata.embeds[0].title or "").casefold()
                descricao = (candidata.embeds[0].description or "").casefold()
                if "trampar conosco" in titulo or "trabalhe conosco" in titulo or "seu talento tem espaço" in descricao:
                    mensagem = candidata
                    break

        if mensagem is None:
            mensagem = await canal.send(view=TramparConoscoView())
            acao = "publicado"
        else:
            # Ao converter uma mensagem antiga para Components V2, limpe explicitamente
            # conteúdo, todos os embeds e anexos conforme exigido pela API do Discord.
            await mensagem.edit(
                content=None,
                embeds=[],
                attachments=[],
                view=TramparConoscoView()
            )
            acao = "atualizado"

        registros["Trampar Conosco"] = {"message_id": mensagem.id}
        save_json(DATA_FILE, settings)
        await interaction.edit_original_response(
            content=f"✅ Painel **Trampar Conosco {acao}** em <#{WORK_WITH_US_CHANNEL_ID}>. Nenhum outro painel ou canal foi alterado.",
            embed=None,
            view=None
        )
        log.info("Painel Trampar Conosco %s pelo /painel (%s), mensagem %s", acao, interaction.user.id, mensagem.id)
    except Exception as exc:
        log.exception("Falha ao atualizar o painel Trampar Conosco pelo /painel")
        detalhe = ""
        if isinstance(exc, discord.HTTPException):
            detalhe = f" (HTTP {exc.status}, código {exc.code})"
            if getattr(exc, "text", None):
                log.error("Resposta HTTP do Discord ao atualizar painel: %s", exc.text[:1000])
        try:
            await interaction.edit_original_response(
                content=f"❌ Não consegui atualizar o painel ({type(exc).__name__}{detalhe}). Confira os logs do bot.",
                embeds=[],
                view=None
            )
        except discord.HTTPException:
            log.exception("Também falhou ao enviar o diagnóstico da atualização do painel")


async def publicar_paineis_comunidade():
    """Atualiza painéis existentes; procura a mensagem antiga antes de criar outra."""
    paineis = [
        ("Parcerias", PARTNERSHIPS_CHANNEL_ID, None, ParceriasView),
        ("Trampar Conosco", WORK_WITH_US_CHANNEL_ID, criar_embed_trampar_conosco, TramparConoscoView),
    ]
    registros = settings.setdefault("community_panels", {})
    for nome, canal_id, criar_embed, criar_view in paineis:
        try:
            canal = bot.get_channel(canal_id)
            if canal is None:
                canal = await bot.fetch_channel(canal_id)
            registro = registros.get(nome, {})
            mensagem_id = registro.get("message_id") if isinstance(registro, dict) else None
            # ID confirmado pelo usuário: reutilizar o painel de parcerias exato.
            if not mensagem_id and nome == "Parcerias":
                mensagem_id = 1562596483688300545
            mensagem = None
            if mensagem_id:
                try:
                    mensagem = await canal.fetch_message(int(mensagem_id))
                except discord.NotFound:
                    mensagem = None

            # Se o ID salvo estiver ausente, tenta localizar o painel já enviado pelo bot.
            # Isso evita publicar uma cópia quando config.json não tem o ID da mensagem.
            if mensagem is None:
                async for candidata in canal.history(limit=100):
                    if candidata.author.id != bot.user.id or not candidata.embeds:
                        continue
                    titulo = (candidata.embeds[0].title or "").casefold()
                    descricao = (candidata.embeds[0].description or "").casefold()
                    if nome == "Trampar Conosco":
                        corresponde = (
                            "trampar conosco" in titulo
                            or "trabalhe conosco" in titulo
                            or "seu talento tem espaço" in descricao
                            or "trampar conosco" in descricao
                        )
                    else:
                        corresponde = "parcerias" in titulo
                    if corresponde:
                        mensagem = candidata
                        break

            if nome == "Trampar Conosco":
                if mensagem is None:
                    mensagem = await canal.send(view=TramparConoscoView())
                    log.info("Painel %s publicado no canal %s (mensagem %s)", nome, canal_id, mensagem.id)
                else:
                    await mensagem.edit(content=None, embeds=[], view=TramparConoscoView())
                    log.info("Painel %s existente atualizado no canal %s (mensagem %s)", nome, canal_id, mensagem.id)
            elif nome == "Parcerias":
                if mensagem is None:
                    mensagem = await canal.send(view=ParceriasView())
                    log.info("Painel %s publicado no canal %s (mensagem %s)", nome, canal_id, mensagem.id)
                else:
                    await mensagem.edit(content=None, embeds=[], view=ParceriasView())
                    log.info("Painel %s existente atualizado no canal %s (mensagem %s)", nome, canal_id, mensagem.id)
            elif mensagem is None:
                mensagem = await canal.send(embed=criar_embed(), view=criar_view())
                log.info("Painel %s publicado no canal %s (mensagem %s)", nome, canal_id, mensagem.id)
            else:
                await mensagem.edit(embed=criar_embed(), view=criar_view())
                log.info("Painel %s existente atualizado no canal %s (mensagem %s)", nome, canal_id, mensagem.id)

            registros[nome] = {"message_id": mensagem.id}
            save_json(DATA_FILE, settings)
        except Exception:
            log.exception("Falha ao publicar/atualizar o painel %s", nome)

SALES_SOURCE_CHANNEL_ID = 1561513284191125625
SALES_COUNTER_CHANNEL_ID = 1561513273554374686
SALES_COUNTER_PREFIX = "│💶﹒Vendas : "

@bot.event
async def on_message(message: discord.Message):
    if message.channel.id != SALES_SOURCE_CHANNEL_ID:
        return

    # Conta toda mensagem nova no canal monitorado, inclusive mensagens sem texto.
    settings["sales_count"] = int(settings.get("sales_count", 0)) + 1
    save_json(DATA_FILE, settings)
    log.info("Mensagem nova; contador de vendas: %s", settings["sales_count"])

async def contar_mensagens_do_canal():
    channel = bot.get_channel(SALES_SOURCE_CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(SALES_SOURCE_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        raise TypeError("O canal monitorado não é um canal de texto.")

    total = 0
    async for _message in channel.history(limit=None, oldest_first=True):
        total += 1

    settings["sales_count"] = total
    save_json(DATA_FILE, settings)
    bot._sales_history_counted = True
    log.info("Contagem recalculada: %s mensagens atuais no canal de vendas", total)

def ajustar_contador_vendas_por_exclusao(quantidade):
    if quantidade <= 0 or not getattr(bot, "_sales_history_counted", False):
        return
    atual = max(0, int(settings.get("sales_count", 0)))
    novo = max(0, atual - quantidade)
    if novo == atual:
        return
    settings["sales_count"] = novo
    save_json(DATA_FILE, settings)
    log.info(
        "Contador de vendas ajustado após exclusão: %s -> %s (mensagens removidas=%s)",
        atual, novo, quantidade
    )

@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    if payload.channel_id != SALES_SOURCE_CHANNEL_ID:
        return
    try:
        if getattr(bot, "_sales_history_counted", False):
            ajustar_contador_vendas_por_exclusao(1)
        else:
            await contar_mensagens_do_canal()
    except Exception:
        log.exception("Falha ao ajustar vendas após exclusão de mensagem")

@bot.event
async def on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent):
    if payload.channel_id != SALES_SOURCE_CHANNEL_ID:
        return
    try:
        if getattr(bot, "_sales_history_counted", False):
            ajustar_contador_vendas_por_exclusao(len(payload.message_ids))
        else:
            await contar_mensagens_do_canal()
    except Exception:
        log.exception("Falha ao ajustar vendas após exclusão em massa")

@tasks.loop(seconds=10)
async def atualizar_contador_vendas():
    try:
        channel = bot.get_channel(SALES_COUNTER_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(SALES_COUNTER_CHANNEL_ID)
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel, discord.CategoryChannel)):
            log.error("Tipo de canal do contador não suportado: %s", type(channel).__name__)
            return

        nome = f"{SALES_COUNTER_PREFIX}{int(settings.get('sales_count', 0))}"
        if channel.name != nome:
            await channel.edit(name=nome, reason="Atualização automática do contador de vendas")
            log.info("Nome do contador alterado para: %s", nome)
    except Exception:
        log.exception("Falha ao atualizar o canal do contador de vendas")

@atualizar_contador_vendas.before_loop
async def antes_de_atualizar_contador_vendas():
    await bot.wait_until_ready()

@bot.tree.command(name="limpar-boas-vindas", description="Apagar as mensagens do canal de boas-vindas")
async def limpar_boas_vindas(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador pode executar esta limpeza.", ephemeral=True
        )
        return
    canal = bot.get_channel(WELCOME_CHANNEL_ID)
    if canal is None:
        try:
            canal = await bot.fetch_channel(WELCOME_CHANNEL_ID)
        except Exception:
            await interaction.response.send_message(
                "Não consegui acessar o canal de boas-vindas.", ephemeral=True
            )
            return
    if not isinstance(canal, discord.TextChannel):
        await interaction.response.send_message(
            "O canal configurado não é um canal de texto.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    apagadas = 0
    try:
        async for mensagem in canal.history(limit=None, oldest_first=True):
            try:
                await mensagem.delete()
                apagadas += 1
            except discord.NotFound:
                continue
            except discord.HTTPException:
                log.exception("Falha ao apagar mensagem %s do canal de boas-vindas.", mensagem.id)
        await interaction.followup.send(
            f"Limpeza concluída: {apagadas} mensagens apagadas de <#{WELCOME_CHANNEL_ID}>. Nenhum comando ou função do bot foi removido.",
            ephemeral=True
        )
        log.info("Limpeza do canal de boas-vindas concluída: %s mensagens apagadas.", apagadas)
    except Exception as exc:
        log.exception("Falha na limpeza do canal de boas-vindas.")
        await interaction.followup.send(
            f"A limpeza parou por um erro ({type(exc).__name__}). Verifique as permissões do bot.",
            ephemeral=True
        )

@bot.event
async def on_member_join(member: discord.Member):
    try:
        roles = await garantir_cargos(member.guild)
        role = roles.get("membro")
        if role and role not in member.roles:
            await member.add_roles(role, reason="Cargo Membro automático ao entrar no servidor")
    except Exception:
        log.exception("Falha ao atribuir cargo Membro na entrada.")
    try:
        channel = bot.get_channel(WELCOME_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(WELCOME_CHANNEL_ID)

        embed = criar_embed_estetico(
            title="Seja bem-vindo(a)",
            description=(
                f"**Eaee, suave? Seja bem-vindo ao nosso server!**\n\n"
                f"Esperamos que você curta a comunidade, {member.mention}.\n\n"
                "**Veja as regras do nosso server:**\n"
                f"[▢𝂽﹒regras](https://discord.com/channels/1561513043886743562/1561513274737168414)"
            ),
            color=discord.Color.green()
        )
        embed.set_author(
            name=member.display_name,
            icon_url=member.display_avatar.url
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)
    except Exception as exc:
        log.exception("Falha ao enviar mensagem de boas-vindas")
        await notify_admin(f"Falha na mensagem de boas-vindas: {exc}")

@bot.event
async def on_member_remove(member: discord.Member):
    try:
        channel = bot.get_channel(WELCOME_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(WELCOME_CHANNEL_ID)

        embed = criar_embed_estetico(
            description=f"**{member.mention} saiu.. sentiremos sua falta**",
            color=discord.Color.red()
        )
        embed.set_author(
            name=member.display_name,
            icon_url=member.display_avatar.url
        )
        embed.set_image(
            url="https://cdn.discordapp.com/ephemeral-attachments/1442529633919570057/1561765993662128148/image0.gif?ex=6ad78b6d&is=6ad639ed&hm=4836ba7ca7239062be07eb91699e555bb5a5d35ebb19b6e112b24241bdefbd83&"
        )
        await channel.send(embed=embed)
    except Exception as exc:
        log.exception("Falha ao enviar mensagem de saída")
        await notify_admin(f"Falha na mensagem de saída: {exc}")

@bot.event
async def restaurar_botoes_beats_existentes():
    """Migra publicações antigas para botões persistentes sem repostar os embeds."""
    guild = bot.get_guild(1561513043886743562)
    if guild is None:
        log.warning("Migração de beats adiada: servidor não está disponível.")
        return

    atualizados = 0
    examinados = 0
    for canal in guild.text_channels:
        try:
            async for mensagem in canal.history(limit=200):
                examinados += 1
                if mensagem.author.id != bot.user.id or not mensagem.embeds:
                    continue
                embed = mensagem.embeds[0]
                rodape = embed.footer.text or "" if embed.footer else ""
                if "Venda de Beats" not in rodape:
                    continue

                nome = (embed.title or "Beat").replace("🎧 ", "", 1).strip()
                preco = next(
                    (campo.value for campo in embed.fields if campo.name.lower() == "valor"),
                    "A combinar"
                )
                registro = beat_records.get(str(mensagem.id), {})
                caminho = registro.get("preview_path", "")
                if not caminho:
                    # Tenta localizar o arquivo local pelo nome do beat.
                    alvo = "".join(c if c.isalnum() or c in "-_ " else "_" for c in nome).strip()
                    candidatos = [p for p in BEAT_PREVIEWS_DIR.iterdir() if p.is_file()]
                    correspondencia = next(
                        (p for p in candidatos if p.stem.casefold() == alvo.casefold()),
                        None
                    )
                    if correspondencia:
                        caminho = str(correspondencia)

                try:
                    await mensagem.edit(view=VendaBeatView(nome, preco, caminho))
                    if caminho:
                        beat_records[str(mensagem.id)] = {"preview_path": caminho}
                    atualizados += 1
                except discord.NotFound:
                    continue
                except discord.HTTPException:
                    log.exception("Não foi possível restaurar botões do beat mensagem=%s", mensagem.id)
        except discord.Forbidden:
            log.warning("Sem permissão para verificar beats no canal %s", canal.id)
        except discord.HTTPException:
            log.exception("Falha ao verificar beats no canal %s", canal.id)

    save_json(BEATS_FILE, beat_records)
    log.info(
        "Migração de botões de beats concluída: %s mensagens atualizadas; %s mensagens verificadas.",
        atualizados, examinados
    )

async def restaurar_paineis_existentes():
    """Reanexa callbacks aos painéis já publicados, editando as mensagens existentes."""
    guild = bot.get_guild(1561513043886743562)
    if guild is None:
        log.warning("Restauração de painéis adiada: servidor não está disponível.")
        return

    conhecidos = [
        ("Gerador de Samples", SAMPLES_PANEL_CHANNEL_ID, SAMPLES_PANEL_MESSAGE_ID, GeradorSamplesView),
        ("Pack Personalizado", PACK_PANEL_CHANNEL_ID, PACK_PANEL_MESSAGE_ID, PackPersonalizadoView),
    ]
    atualizados = 0
    vistos = set()

    # Atualiza as mensagens conhecidas pelos IDs, sem criar cópias.
    for nome, canal_id, mensagem_id, criar_view in conhecidos:
        try:
            canal = bot.get_channel(canal_id) or await bot.fetch_channel(canal_id)
            mensagem = await canal.fetch_message(mensagem_id)
            await mensagem.edit(view=criar_view())
            vistos.add(mensagem.id)
            atualizados += 1
            log.info("Painel existente restaurado: %s canal=%s mensagem=%s", nome, canal_id, mensagem_id)
        except discord.NotFound:
            log.warning("Painel conhecido não encontrado: %s canal=%s mensagem=%s", nome, canal_id, mensagem_id)
        except Exception:
            log.exception("Falha ao restaurar painel conhecido: %s", nome)

    # Procura outros painéis pelos seus embeds e atualiza as mensagens encontradas.
    for canal in guild.text_channels:
        try:
            async for mensagem in canal.history(limit=200):
                if mensagem.id in vistos or mensagem.author.id != bot.user.id or not mensagem.embeds:
                    continue

                embed = mensagem.embeds[0]
                titulo = (embed.title or "").casefold()
                rodape = (embed.footer.text or "").casefold() if embed.footer else ""
                view = None
                nome = None

                if "arrumador de projetos" in rodape or "arrumador de arquivos" in titulo:
                    view, nome = ArrumadorPainelView(), "Arrumador de Projetos"
                elif "busca de acapellas" in rodape or "acapellas de mcs famosos" in titulo:
                    view, nome = AcapellasActivityView(), "Acapellas"
                elif "c.c © • trampar conosco" in rodape:
                    view, nome = TramparConoscoView(), "Trampar Conosco"
                elif "venda de beats" in rodape:
                    # Beats são restaurados pela migração específica, que recupera a prévia.
                    continue

                if view is None:
                    continue

                try:
                    await mensagem.edit(view=view)
                    vistos.add(mensagem.id)
                    atualizados += 1
                    log.info("Painel existente restaurado: %s canal=%s mensagem=%s", nome, canal.id, mensagem.id)
                except discord.NotFound:
                    continue
                except discord.HTTPException:
                    log.exception("Falha ao editar painel existente: %s mensagem=%s", nome, mensagem.id)
        except discord.Forbidden:
            log.warning("Sem permissão para ler histórico do canal %s durante restauração.", canal.id)
        except discord.HTTPException:
            log.exception("Falha ao examinar histórico do canal %s durante restauração.", canal.id)

    log.info("Restauração de painéis concluída: %s mensagens existentes atualizadas.", atualizados)

# Categoria pública com explicação do fluxo de compras. Atualiza a mensagem existente sem duplicar.
SALES_INFO_CATEGORY = "Vendas"
SALES_INFO_CHANNEL = "como-funcionam-as-vendas"
SALES_INFO_TITLE = "🛒 COMO FUNCIONAM AS VENDAS"

async def garantir_painel_info_vendas(guild):
    if guild is None:
        return
    categoria = guild.get_channel(1561513260501569576)
    if not isinstance(categoria, discord.CategoryChannel):
        categoria = next((cat for cat in guild.categories if cat.name.strip().casefold() in {"information", "informações", "informacoes", "info"}), None)
    if categoria is None:
        categoria = await guild.create_category("INFORMAÇÕES", reason="Área de informações do servidor")

    canal = next((ch for ch in guild.text_channels if ch.name == SALES_INFO_CHANNEL and ch.category_id == categoria.id), None)
    if canal is None:
        canal = discord.utils.get(guild.text_channels, name=SALES_INFO_CHANNEL)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=False,
            add_reactions=False, create_public_threads=False, create_private_threads=False
        )
    }
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=True,
            embed_links=True, attach_files=True, manage_messages=True
        )

    if canal is None:
        canal = await guild.create_text_channel(
            SALES_INFO_CHANNEL,
            category=categoria,
            overwrites=overwrites,
            topic="Leia aqui como funciona o pagamento, a conferência manual e a entrega dos pedidos.",
            reason="Publicar instruções de compra do CC BOT"
        )
    else:
        await canal.edit(category=categoria, overwrites=overwrites)

    embed = criar_embed_estetico(
        title=SALES_INFO_TITLE,
        description=(
            "As compras são liberadas **após a confirmação manual do pagamento**. "
            "Veja o passo a passo:\n\n"
            "🛍️ **1. Escolha o produto**\n"
            "Inicie a compra pelo painel e siga as instruções do pedido privado.\n\n"
            "💸 **2. Faça o Pix**\n"
            "Pague o valor mostrado no pedido. Use o QR Code ou o botão **Copiar código Pix**.\n\n"
            "🧾 **3. Envie o comprovante**\n"
            "Depois de pagar, mande a imagem ou o PDF do comprovante no próprio canal do pedido. "
            "O comprovante será encaminhado ao administrador.\n\n"
            "👨‍💻 **4. Aguarde a conferência do ADM**\n"
            "O administrador recebe mensagens de aviso sobre os pedidos e confere o pagamento manualmente. "
            "Até essa verificação, o pedido fica como **aguardando confirmação**.\n\n"
            "📦 **5. Receba seu produto**\n"
            "Após o ADM confirmar o pagamento, o produto é liberado ou enviado normalmente em **poucas horas**. "
            "O prazo pode variar conforme o produto e a demanda.\n\n"
            "⚠️ **Importante:** enviar comprovante não confirma automaticamente o pagamento. "
            "Não abra pedidos duplicados para a mesma compra; aguarde a resposta do ADM no seu pedido."
        ),
        color=discord.Color.dark_grey()
    )
    embed.set_footer(text="CC BOT • Pagamentos conferidos manualmente")
    mensagem_existente = None
    async for mensagem in canal.history(limit=50):
        if mensagem.author.id == bot.user.id and mensagem.embeds and mensagem.embeds[0].title == SALES_INFO_TITLE:
            mensagem_existente = mensagem
            break
    if mensagem_existente:
        await mensagem_existente.edit(embed=embed)
    else:
        await canal.send(embed=embed)

    # Remove apenas duplicatas exatas do mesmo guia, mantendo uma única versão em INFORMAÇÕES.
    for duplicado in list(guild.text_channels):
        if duplicado.id != canal.id and duplicado.name == SALES_INFO_CHANNEL:
            try:
                await duplicado.delete(reason="Remover canal duplicado de como funcionam as vendas")
            except discord.HTTPException:
                log.exception("Não foi possível remover o canal duplicado %s.", duplicado.id)
    for cat_antiga in list(guild.categories):
        if cat_antiga.name.strip().casefold() in {"vendas", "sales information", "informações de vendas", "informacoes de vendas"} and not cat_antiga.channels:
            try:
                await cat_antiga.delete(reason="Remover categoria redundante de vendas")
            except discord.HTTPException:
                log.exception("Não foi possível remover categoria redundante de vendas.")
    log.info("Canal único de informações de vendas garantido em #%s.", canal.name)

async def renomear_categorias_principais(guild: discord.Guild):
    """Renomeia as categorias principais pelos IDs, sem depender do painel de estrutura."""
    if guild is None:
        return
    me = guild.me
    if me is None or not me.guild_permissions.manage_channels:
        log.warning("Sem permissão Gerenciar canais; categorias principais não foram renomeadas.")
        return

    alteradas = 0
    for item in HIERARQUIA_CATEGORIAS.values():
        categoria = guild.get_channel(item["id"])
        if not isinstance(categoria, discord.CategoryChannel):
            log.warning("Categoria principal não encontrada: id=%s nome_destino=%s", item["id"], item["nome"])
            continue
        if categoria.name != item["nome"]:
            try:
                await categoria.edit(name=item["nome"], reason="Atualização automática dos nomes das categorias principais")
                alteradas += 1
                log.info("Categoria renomeada: id=%s nome=%s", categoria.id, item["nome"])
            except Exception:
                log.exception("Falha ao renomear categoria id=%s para %s", categoria.id, item["nome"])
    # A categoria de informações de vendas também deve ficar em inglês.
    categoria_vendas = discord.utils.get(guild.categories, name=SALES_INFO_CATEGORY)
    if categoria_vendas is None:
        categoria_vendas = discord.utils.get(guild.categories, name="INFORMAÇÕES DE VENDAS")
    if categoria_vendas is not None and categoria_vendas.name != SALES_INFO_CATEGORY:
        try:
            await categoria_vendas.edit(name=SALES_INFO_CATEGORY, reason="Padronização automática de nomes em inglês")
            alteradas += 1
            log.info("Categoria de vendas renomeada: %s", SALES_INFO_CATEGORY)
        except Exception:
            log.exception("Falha ao renomear a categoria de informações de vendas.")
    # Traduz nomes comuns de categorias antigas sem tocar na categoria de status "CC •".
    traducoes = {
        "personalização": "customization", "personalizacao": "customization",
        "ícones": "customization", "icones": "customization",
        "comunidade": "community", "estatísticas": "statistics", "estatisticas": "statistics",
        "administração": "administration", "administracao": "administration",
        "informações": "information", "informacoes": "information",
        "lojas": "partners", "parceiros": "partners", "mercado": "cc store",
        "informações de vendas": SALES_INFO_CATEGORY, "informacoes de vendas": SALES_INFO_CATEGORY,
    }
    for categoria in list(guild.categories):
        nome_atual = categoria.name.strip().casefold()
        if "cc •" in nome_atual or "cc ·" in nome_atual:
            continue
        destino = traducoes.get(nome_atual)
        if destino and categoria.name != destino and not discord.utils.get(guild.categories, name=destino):
            try:
                await categoria.edit(name=destino, reason="Traduzir categoria antiga para inglês")
                alteradas += 1
            except Exception:
                log.exception("Falha ao traduzir categoria id=%s", categoria.id)
    log.info("Atualização automática de nomes concluída: %s categoria(s) renomeada(s).", alteradas)

async def aplicar_organizacao_automatica(guild: discord.Guild):
    """Organiza categorias e canais separadamente, sem depender do painel manual."""
    if guild is None or guild.me is None or not guild.me.guild_permissions.manage_channels:
        log.warning("Organização automática ignorada: servidor ausente ou sem Gerenciar canais.")
        return False

    try:
        canais = await guild.fetch_channels()

        # Substitui o prefixo antigo dos canais pelo novo, preservando o restante do nome.
        prefixo_antigo = re.compile(r"^\s*▢𝂽﹒\s*")
        renomeados_prefixo = 0
        for canal in canais:
            nome_atual = getattr(canal, "name", "")
            nome_novo = prefixo_antigo.sub("◞﹒", nome_atual, count=1)
            if nome_novo != nome_atual:
                try:
                    await canal.edit(name=nome_novo, reason="Atualização do prefixo visual dos canais CC BOT")
                    renomeados_prefixo += 1
                except (discord.Forbidden, discord.HTTPException):
                    log.exception("Não consegui atualizar o prefixo do canal %s", nome_atual)
        if renomeados_prefixo:
            log.info("Prefixo atualizado em %s canal(is).", renomeados_prefixo)

        categorias = [x for x in canais if isinstance(x, discord.CategoryChannel)]

        # Junta categorias pequenas às áreas principais, sem apagar canais ou mensagens.
        # Só mescla áreas semanticamente relacionadas; os canais mantêm suas permissões próprias.
        categorias_mescladas = {}
        regras_mescla = (
            (("sales information", "informações de vendas", "informacoes de vendas", "vendas"), ("cc store", "mercado"), "MERCADO"),
            (("statistics", "estatísticas", "estatisticas", "stats"), ("information", "informações", "informacao", "informação", "info"), "INFO"),
            (("partners", "parceiros", "parcerias"), ("community", "comunidade"), "COMUNIDADE"),
        )
        def normalizar_categoria(nome):
            return " ".join(nome.strip().casefold().split())
        for nomes_origem, nomes_destino, rotulo_destino in regras_mescla:
            origem = next((cat for cat in categorias if normalizar_categoria(cat.name) in nomes_origem), None)
            destino = next((cat for cat in categorias if cat is not origem and normalizar_categoria(cat.name) in nomes_destino), None)
            if origem is None or destino is None or origem.id == destino.id:
                continue
            movidos = []
            for filho in list(origem.channels):
                try:
                    await filho.edit(category=destino, reason=f"Organização CC BOT: mesclar {origem.name} em {rotulo_destino}")
                    movidos.append(filho.id)
                except (discord.Forbidden, discord.HTTPException):
                    log.exception("Não consegui mover %s da categoria %s para %s", filho.name, origem.name, destino.name)
            if not origem.channels:
                try:
                    await origem.delete(reason=f"Categoria desnecessária mesclada em {rotulo_destino}")
                    categorias = [cat for cat in categorias if cat.id != origem.id]
                    categorias_mescladas.setdefault(destino.id, []).extend(movidos)
                    log.info("Categoria %s mesclada em %s; %s canal(is) movido(s).", origem.name, destino.name, len(movidos))
                except (discord.Forbidden, discord.HTTPException):
                    log.exception("Canais movidos, mas não consegui excluir categoria vazia %s", origem.name)
            else:
                log.warning("Categoria %s mantida porque ainda contém canais que não puderam ser movidos.", origem.name)


        status = next((
            x for x in categorias
            if any(p in x.name.casefold() for p in ("cc •", "cc ·", "server status", "status do servidor"))
        ), None)

        custom = next((
            x for x in categorias
            if x.name.casefold() in ("customization", "customisation", "custom", "customização", "customizacao", "personalização", "personalizacao", "ícones", "icones", "icons")
        ), None)
        if custom is None:
            custom = await guild.create_category("customization", reason="Organização automática CC BOT")
        elif custom.name != "customization":
            await custom.edit(name="customization", reason="Organização automática CC BOT")

        free = next((
            x for x in categorias
            if x.name.casefold() in ("free things", "free stuff", "coisas grátis", "coisas gratis", "grátis", "gratis")
        ), None)
        if free is None:
            free = await guild.create_category("free things", reason="Organização automática CC BOT")
        elif free.name != "free things":
            await free.edit(name="free things", reason="Organização automática CC BOT")

        # Move apenas os canais conhecidos para suas categorias corretas.
        for cid, dest in (
            (1561513290473934998, custom),
            (1561513293934362705, free),
            (1561513295352037507, free),
        ):
            ch = guild.get_channel(cid)
            if ch and ch.category_id != dest.id:
                await ch.edit(category=dest, reason="Organização automática CC BOT")

        # Padroniza os nomes dos canais administrativos sem apagar mensagens.
        for ch in list(guild.text_channels):
            if ch.name == "confirmar-pagamentos":
                await ch.edit(name="pc-confirmar-pagamentos", reason="Padronização visual administrativa")
            elif ch.name == "pagamentos-cancelados":
                await ch.edit(name="cpp-cancelamentos", reason="Padronização visual administrativa")

        # Rebusca depois de renomear/mover; Sales Information pode ter sido criada antes
        # ou depois desta função, então é identificada pelo nome e fica junto da loja.
        canais = await guild.fetch_channels()
        categorias = [x for x in canais if isinstance(x, discord.CategoryChannel)]
        por_id = {x.id: x for x in categorias}

        status = next((
            x for x in categorias
            if any(p in x.name.casefold() for p in ("cc •", "cc ·", "server status", "status do servidor"))
        ), None)
        custom = por_id.get(custom.id)
        free = por_id.get(free.id)

        ids_principais = [
            1561513261424447609,  # partners
            1561513258970521743,  # statistics
            1561513263286452275,  # cc store
        ]
        categoria_vendas = next((
            x for x in categorias
            if x.name.strip().casefold() in ("sales information", "informações de vendas", "informacoes de vendas", "vendas")
        ), None)
        comunidade = por_id.get(1561513265006116877)
        informacao = por_id.get(1561513260501569576)
        administracao = por_id.get(1561513258211352626)

        # A ordem é aplicada SOMENTE às categorias, nunca à lista misturada de canais.
        ordem_preferida = []
        sites = next((
            x for x in categorias
            if x.name.strip().casefold() in ("pattern", "sites")
        ), None)
        for cat in (
            status, custom, sites,
            por_id.get(1561513263286452275),  # Mercado
            categoria_vendas,                 # explicação das vendas junto do mercado
            comunidade,
            free,
            informacao,
            administracao,
            por_id.get(1561513261424447609),  # Parceiros
            por_id.get(1561513258970521743),  # Estatísticas
        ):
            if cat is not None and cat not in ordem_preferida:
                ordem_preferida.append(cat)

        # Mantém todas as demais categorias; nenhuma é excluída ou recriada.
        ordem_preferida.extend(
            cat for cat in sorted(categorias, key=lambda z: z.position)
            if cat not in ordem_preferida
        )
        for pos, cat in enumerate(ordem_preferida):
            if cat.position != pos:
                try:
                    await cat.edit(position=pos, reason="Hierarquia automática CC BOT — ordem de categorias")
                except discord.HTTPException:
                    log.warning("Não consegui posicionar categoria %s (%s)", cat.name, cat.id)

        # Ordena apenas os canais internos das categorias conhecidas.
        canais_por_categoria = {
            1561513258970521743: [1561513270819692606, 1561513273554374686, 1561513275403800709],
            1561513263286452275: [1561513284811624480, 1561513286967500841, 1561513288297353286, 1561513289438199878, 1561513284191125625],
            1561513265006116877: [1561513291703128106, 1561513297155592253, 1561513293124739173],
            1561513260501569576: [1561513269754068992, 1561513274737168414, 1561513277207347310, 1561513277882634404, 1561837934649409696],
            1561513258211352626: [1561513268021821460, 1561698571525423115],
            1561513261424447609: [1561513281049595958, 1561513282265944104, 1561513283171651644],
        }
        canais_por_categoria[custom.id] = [1561513290473934998]
        canais_por_categoria[free.id] = [1561513293934362705, 1561513295352037507]

        # Inclui na ordenação os canais trazidos das categorias mescladas.
        for destino_id, ids_mesclados in categorias_mescladas.items():
            canais_por_categoria.setdefault(destino_id, [])
            canais_por_categoria[destino_id].extend(cid for cid in ids_mesclados if cid not in canais_por_categoria[destino_id])

        # Posiciona canais listados no início da categoria e preserva os demais depois.
        for cat_id, ids in canais_por_categoria.items():
            cat = por_id.get(cat_id)
            if cat is None:
                continue
            filhos = sorted(cat.channels, key=lambda z: z.position)
            ordenados = [guild.get_channel(cid) for cid in ids]
            ordenados = [ch for ch in ordenados if ch is not None and ch.category_id == cat.id]
            ordenados.extend(ch for ch in filhos if ch not in ordenados)
            for pos, ch in enumerate(ordenados):
                if ch.position != pos:
                    try:
                        await ch.edit(position=pos, reason="Hierarquia automática CC BOT — canais internos")
                    except discord.HTTPException:
                        log.warning("Não consegui posicionar canal %s (%s)", ch.name, ch.id)

        log.info("Hierarquia aplicada: Status, Customization, Partners, Statistics, CC Store, Sales Information, Community, Free Things, Information e Administration.")
        return True
    except Exception:
        log.exception("Falha ao aplicar organização automática do servidor.")
        return False

async def ordenar_cargos_principais(guild: discord.Guild) -> int:
    """Ordena os cargos públicos principais sem ultrapassar o cargo do próprio bot."""
    if guild is None or guild.me is None or not guild.me.guild_permissions.manage_roles:
        log.warning("Sem permissão Gerenciar cargos; ordem dos cargos não foi alterada.")
        return 0

    nomes = ("Fundador", "Staff", "Parceiro", "DJ / Produtor", "Cliente", "Membro")
    por_nome = {role.name.casefold(): role for role in guild.roles}
    alvos = [por_nome[nome.casefold()] for nome in nomes if nome.casefold() in por_nome]
    bot_role = guild.me.top_role
    if not alvos or bot_role.position <= len(alvos):
        log.warning("Não há espaço seguro abaixo do cargo do bot para ordenar os cargos principais.")
        return 0

    # Discord interpreta posições maiores como cargos mais altos. Mantém o cargo do bot acima.
    posicoes = {
        role: bot_role.position - index - 1
        for index, role in enumerate(alvos)
        if not role.managed and role < bot_role
    }
    if not posicoes:
        return 0
    try:
        await guild.edit_role_positions(
            positions=posicoes,
            reason="Organização da hierarquia de cargos pelo CC BOT"
        )
        log.info("Cargos principais reposicionados: %s", ", ".join(role.name for role in posicoes))
        return len(posicoes)
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Falha ao ordenar cargos principais.")
        return 0


async def renomear_categorias_solicitadas(guild: discord.Guild):
    """Padroniza categorias com nomes curtos e fáceis de entender."""
    if guild is None or guild.me is None or not guild.me.guild_permissions.manage_channels:
        log.warning("Sem permissão Gerenciar canais; nomes das categorias não foram alterados.")
        raise PermissionError("O CC BOT precisa da permissão Gerenciar canais.")

    traducoes = {
        "customization": "CUSTOM", "customisation": "CUSTOM", "custom": "CUSTOM",
        "personalização": "CUSTOM", "personalizacao": "CUSTOM",
        "icons": "ÍCONES", "ícones": "ÍCONES", "icones": "ÍCONES",
        "administration": "ADMIN", "administração": "ADMIN", "administracao": "ADMIN",
        "information": "INFO", "informação": "INFO", "informacoes": "INFO", "informações": "INFO",
        "pattern": "SITES", "sites": "SITES",
        "sales information": "VENDAS", "informações de vendas": "VENDAS", "informacoes de vendas": "VENDAS", "vendas": "VENDAS",
        "community": "COMUNIDADE", "comunidade": "COMUNIDADE",
        "free things": "GRÁTIS", "free stuff": "GRÁTIS", "coisas grátis": "GRÁTIS", "coisas gratis": "GRÁTIS", "grátis": "GRÁTIS", "gratis": "GRÁTIS",
        "partners": "PARCERIAS", "parceiros": "PARCERIAS", "parcerias": "PARCERIAS",
        "statistics": "STATS", "estatísticas": "STATS", "estatisticas": "STATS", "stats": "STATS",
        "cc store": "MERCADO", "mercado": "MERCADO",
    }
    alteradas = 0
    for categoria in list(guild.categories):
        nome_atual = categoria.name.strip().casefold()
        if "cc •" in nome_atual or "cc ·" in nome_atual or nome_atual == "cc story":
            continue
        destino = traducoes.get(nome_atual)
        if not destino or categoria.name == destino:
            continue
        outra = next((cat for cat in guild.categories if cat.id != categoria.id and cat.name.casefold() == destino.casefold()), None)
        if outra:
            log.warning("Categoria %s não renomeada para %s: já existe outra categoria com esse nome.", categoria.id, destino)
            continue
        try:
            nome_anterior = categoria.name
            await categoria.edit(name=destino, reason="Simplificação dos nomes do servidor pelo CC BOT")
            alteradas += 1
            log.info("Categoria simplificada: id=%s %r -> %r", categoria.id, nome_anterior, destino)
        except Exception:
            log.exception("Falha ao renomear categoria id=%s para %s", categoria.id, destino)
    log.info("Simplificação de categorias concluída: %s categoria(s).", alteradas)
    return alteradas

# FREE_PROJECTS_PANEL_V2: os botões abrem tópicos privados; não criam categorias nem canais.
FREE_PROJECTS_CONFIG_KEY = "free_projects_panels"
FREE_PROJECTS_GUILD_ID = 1561513043886743562

FREE_PROJECTS = {
    "packs": {
        "channel_id": 1561513293934362705,
        "title": "📦 𝗣𝗔𝗖𝗞𝗦 𝗚𝗥𝗔́𝗧𝗜𝗦",
        "description": "Uma área para descobrir packs gratuitos e encontrar materiais que podem acrescentar novas ideias à sua produção. Abra seu espaço privado pelo botão abaixo e confira as orientações de acesso.",
        "custom_id": "cc:free-projects:packs",
        "button_label": "Ver packs",
        "thread_prefix": "packs"
    },
    "projetos": {
        "channel_id": 1561513295352037507,
        "title": "🎛️ 𝗣𝗥𝗢𝗝𝗘𝗧𝗢𝗦 𝗚𝗥𝗔́𝗧𝗜𝗦",
        "description": "Explore projetos gratuitos para estudar estruturas, testar ideias e conhecer novas formas de organizar suas produções. Abra seu espaço privado pelo botão abaixo para receber as orientações de acesso.",
        "custom_id": "cc:free-projects:projetos",
        "button_label": "Ver projetos",
        "thread_prefix": "projetos"
    }
}

class FreeProjectsAccessView(discord.ui.View):
    def __init__(self, kind):
        super().__init__(timeout=None)
        self.kind = kind
        cfg = FREE_PROJECTS[kind]
        button = discord.ui.Button(
            label=cfg["button_label"],
            emoji="📦" if kind == "packs" else "🎛️",
            style=discord.ButtonStyle.primary,
            custom_id=cfg["custom_id"]
        )
        button.callback = self.open_private_thread
        self.add_item(button)

    async def open_private_thread(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "Use este botão em um canal de texto do servidor.", ephemeral=True
            )
            return

        cfg = FREE_PROJECTS[self.kind]
        channel = interaction.channel
        thread_name = f"{cfg['thread_prefix']}-{interaction.user.id}"
        try:
            thread = next(
                (
                    item for item in channel.threads
                    if item.name == thread_name and not item.archived
                ),
                None
            )
            created = thread is None
            if thread is None:
                thread = await channel.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                    auto_archive_duration=1440,
                    reason=f"Tópico privado de {cfg['title']} solicitado por {interaction.user}"
                )
            await thread.add_user(interaction.user)
            if created:
                embed = criar_embed_estetico(
                    title=cfg["title"],
                    description=(
                        f"Este é seu tópico privado de **{cfg['button_label'].lower()}**.\n"
                        "Use este espaço para acessar o conteúdo compartilhado aqui."
                    ),
                    color=discord.Color.from_rgb(20, 20, 20)
                )
                embed.set_footer(text="C.C © • Conteúdo gratuito")
                await thread.send(embed=embed)
            await interaction.response.send_message(
                f"✅ Seu tópico está pronto: {thread.mention}", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Não consegui abrir o tópico. O bot precisa das permissões **Criar tópicos privados**, **Gerenciar tópicos** e **Enviar mensagens**.",
                ephemeral=True
            )
        except discord.HTTPException:
            log.exception("Falha ao abrir tópico grátis para usuário %s (%s)", interaction.user.id, self.kind)
            await interaction.response.send_message(
                "Ocorreu um erro ao abrir seu tópico. Tente novamente mais tarde.",
                ephemeral=True
            )

async def configurar_paineis_gratis(guild):
    """Atualiza os painéis existentes, remove as categorias extras e não cria canais."""
    if guild.id != FREE_PROJECTS_GUILD_ID:
        return

    # Mantém os canais de painel e move-os para GRÁTIS (ou sem categoria) antes da limpeza.
    categorias_indesejadas = {
        "📦 PACKS GRÁTIS".casefold(),
        "🎛️ PROJETOS GRÁTIS".casefold(),
    }
    categorias = await guild.fetch_channels()
    free_category = next(
        (
            item for item in categorias
            if isinstance(item, discord.CategoryChannel)
            and item.name.strip().casefold() in ("free things", "grátis", "gratis", "coisas grátis", "coisas gratis")
        ),
        None
    )
    for kind, cfg in FREE_PROJECTS.items():
        channel = guild.get_channel(cfg["channel_id"])
        if channel is None:
            try:
                channel = await guild.fetch_channel(cfg["channel_id"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.error("Canal do painel grátis não encontrado; não será criado: %s", cfg["channel_id"])
                continue
        if not isinstance(channel, discord.TextChannel):
            log.error("O destino do painel grátis não é canal de texto: %s", cfg["channel_id"])
            continue
        if channel.category and channel.category.name.strip().casefold() in categorias_indesejadas:
            try:
                await channel.edit(
                    category=free_category,
                    reason="Remover categoria extra de packs/projetos grátis e preservar o painel"
                )
            except discord.HTTPException:
                log.exception("Falha ao mover painel grátis para fora da categoria extra: %s", channel.id)
                continue

        try:
            view = FreeProjectsAccessView(kind)
            saved = settings.get(FREE_PROJECTS_CONFIG_KEY, {})
            record = saved.get(kind, {}) if isinstance(saved, dict) else {}
            message = None
            message_id = record.get("message_id") if isinstance(record, dict) else None
            if message_id:
                try:
                    message = await channel.fetch_message(int(message_id))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = None
            if message is None:
                async for candidate in channel.history(limit=50):
                    if candidate.author.id == bot.user.id and candidate.components:
                        ids = {
                            getattr(child, "custom_id", None)
                            for row in candidate.components
                            for child in getattr(row, "children", [])
                        }
                        if cfg["custom_id"] in ids:
                            message = candidate
                            break
            embed = criar_embed_estetico(
                title=cfg["title"],
                description=f"**Conteúdo gratuito para suas produções.**\n-# PAINEL ATUALIZADO\n\n{cfg['description']}",
                color=discord.Color.from_rgb(20, 20, 20)
            )
            embed.set_footer(text="C.C © • Clique para abrir seu tópico privado")
            if message is None:
                message = await channel.send(embed=embed, view=view)
            else:
                await message.edit(embed=embed, view=view)
            saved = saved if isinstance(saved, dict) else {}
            saved[kind] = {"channel_id": channel.id, "message_id": message.id}
            settings[FREE_PROJECTS_CONFIG_KEY] = saved
            save_json(DATA_FILE, settings)
            log.info("Painel grátis atualizado em #%s; botão abre tópico privado.", channel.name)
        except Exception:
            log.exception("Falha ao configurar painel grátis: %s", kind)

    # Exclui apenas as categorias criadas pelo fluxo antigo. Os painéis são movidos antes.
    for category in list(guild.categories):
        if category.name.strip().casefold() in categorias_indesejadas:
            try:
                await category.delete(reason="Remover categorias extras; acesso passa a usar tópicos privados")
                log.info("Categoria extra removida: %s", category.name)
            except discord.HTTPException:
                log.exception("Falha ao remover categoria extra %s", category.name)

@bot.event
async def on_ready():
    try:
        guild = discord.Object(id=1561513043886743562)

        # on_ready pode disparar novamente após reconexões. Sincronizar comandos
        # em toda chamada gera PUTs repetidos e pode causar HTTP 429.
        # Inicializa a variável também quando a sincronização já ocorreu nesta execução.
        synced = bot.tree.get_commands(guild=guild)
        if not getattr(bot, "_commands_synced_this_process", False):
            # Mantém os comandos ativos apenas no servidor de destino.
            bot.tree.clear_commands(guild=guild)
            bot.tree.copy_global_to(guild=guild)
            # Sincronizamos somente a árvore do servidor. Manter a árvore global em memória
            # permite tentar novamente após uma falha sem perder os comandos locais.
            try:
                synced = await bot.tree.sync(guild=guild)
                bot._commands_synced_this_process = True
                log.info("Comandos sincronizados somente no servidor %s: %s", guild.id, len(synced))
            except Exception:
                log.exception("Falha ao sincronizar comandos; será tentado novamente na próxima conexão.")
                raise
        else:
            log.info("Sincronização de comandos já realizada nesta execução; ignorando repetição.")
        log.info("Conectado como %s", bot.user)

        # Não publicar nem reconfigurar painéis em massa durante a inicialização.
        # A configuração de painéis gratuitos deve ser acionada manualmente.

        # Limpeza única do histórico antigo de Confirmar pagamentos, solicitada pelo administrador.
        if settings.get("payment_admin_history_cleared") is False:
            servidor_limpeza = bot.get_guild(1561513043886743562)
            if servidor_limpeza is None:
                raise RuntimeError("Servidor não encontrado para limpar o histórico de pagamentos.")
            canal_limpeza = await obter_canal_confirmacao(servidor_limpeza)
            apagadas = 0
            async for mensagem_antiga in canal_limpeza.history(limit=None, oldest_first=True):
                try:
                    await mensagem_antiga.delete()
                    apagadas += 1
                except discord.NotFound:
                    continue
            settings["payment_admin_history_cleared"] = True
            save_json(DATA_FILE, settings)
            log.info("Limpeza única de #%s concluída: %s mensagens apagadas.", canal_limpeza.name, apagadas)

        # A inicialização não cria/move canais, reescreve permissões nem cria/colori cargos.
        # Os canais de pagamento são preparados sob demanda no fluxo de pagamento.
        # A hierarquia do servidor só pode ser alterada por ação explícita do administrador.
        servidor_cc = bot.get_guild(1561513043886743562)

        # Atualiza somente a mensagem de suporte configurada pelo administrador.
        # A função busca a mensagem pelo ID e nunca cria uma duplicata.
        if not getattr(bot, "_support_panel_updated_this_process", False):
            try:
                await garantir_painel_suporte(servidor_cc)
                bot._support_panel_updated_this_process = True
                log.info("Verificação do painel de suporte concluída.")
            except Exception:
                log.exception("Não foi possível atualizar a mensagem existente do painel de suporte.")

        # Migra painéis legados uma única vez; os callbacks persistentes são registrados em setup_hook.
        if not settings.get("legacy_panels_restored", False):
            await restaurar_paineis_existentes()
            settings["legacy_panels_restored"] = True
            save_json(DATA_FILE, settings)

        # Migra botões de beats antigos uma única vez. Novos beats já nascem com IDs persistentes.
        if not settings.get("beats_buttons_migrated", False):
            await restaurar_botoes_beats_existentes()
            settings["beats_buttons_migrated"] = True
            save_json(DATA_FILE, settings)

        # Ponte GitHub: recebe tarefas aprovadas no repositório e publica relatórios.
        tarefa_ponte = getattr(bot, "_github_bridge_task", None)
        if tarefa_ponte is None or tarefa_ponte.done():
            bot._github_bridge_task = asyncio.create_task(github_bridge.run(bot))

        if not getattr(bot, "_sales_history_counted", False):
            await contar_mensagens_do_canal()
            bot._sales_history_counted = True

        if not atualizar_contador_vendas.is_running():
            atualizar_contador_vendas.start()

        # Startup silencioso: não envia avisos nem publica painéis em canais.
        # Os comandos e as views persistentes continuam funcionando normalmente.

        # O sistema de ícones agora importa ZIPs manualmente; não existe monitoramento de pastas.


    except Exception as exc:
        log.exception("Falha ao iniciar ou sincronizar comandos")
        await notify_admin(f"Falha ao iniciar o CC BOT: {exc}")

async def anexar_comprovantes_ao_pedido(pedido, canal_admin, arquivos, nomes_arquivos):
    """Anexa arquivos à mensagem administrativa original, preservando os botões de aprovação."""
    if not arquivos or len(arquivos) != len(nomes_arquivos):
        raise ValueError("Lista de comprovantes vazia ou inconsistente.")

    mensagem_id = pedido.get("admin_message_id")
    if not mensagem_id:
        raise RuntimeError("O pedido não possui ID da mensagem administrativa original.")
    mensagem_admin = await canal_admin.fetch_message(int(mensagem_id))
    if not mensagem_admin.embeds:
        raise RuntimeError("A mensagem administrativa do pedido não contém o embed esperado.")
    if len(mensagem_admin.attachments) + len(arquivos) > 10:
        raise ValueError("O pedido já atingiu o limite de 10 anexos no Discord.")

    embed = mensagem_admin.embeds[0]
    nome_campo = "📎 Comprovante(s) recebido(s)"
    linhas = "\n".join("• " + str(nome).replace(chr(96), "'") for nome in nomes_arquivos)
    indice = next(
        (i for i, campo in enumerate(embed.fields) if campo.name.casefold() == nome_campo.casefold()),
        None
    )
    if indice is None:
        embed.add_field(name=nome_campo, value=linhas[:1024], inline=False)
    else:
        valor_atual = embed.fields[indice].value
        embed.set_field_at(
            indice,
            name=nome_campo,
            value=(valor_atual + "\n" + linhas)[:1024],
            inline=False
        )

    imagem = next(
        (nome for nome in nomes_arquivos if Path(nome).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}),
        None
    )
    if imagem:
        embed.set_image(url=f"attachment://{imagem}")

    await mensagem_admin.edit(
        embed=embed,
        attachments=[*mensagem_admin.attachments, *arquivos],
        view=ConfirmarPagamentoView()
    )
    pedido["receipt_received_at"] = datetime.now().isoformat(timespec="seconds")
    pedido["receipt_filename"] = ", ".join(nomes_arquivos)[:1000]
    pedido["receipt_message_id"] = str(mensagem_admin.id)
    save_json(PIX_ORDERS_FILE, pix_orders)
    return mensagem_admin


@bot.tree.command(name="comprovante", description="Enviar o arquivo do comprovante Pix sem publicar no chat")
@app_commands.describe(arquivo="Imagem ou PDF do comprovante (até 10 MB)")
async def enviar_comprovante_pix(interaction: discord.Interaction, arquivo: discord.Attachment):
    if interaction.guild is None:
        await interaction.response.send_message("Use este comando dentro do canal privado do seu pedido.", ephemeral=True)
        return

    pedido = next(
        (
            item for item in pix_orders.values()
            if str(item.get("ticket_channel_id")) == str(interaction.channel_id)
            and str(item.get("discord_id")) == str(interaction.user.id)
            and item.get("status") == "aguardando_confirmacao_manual"
        ),
        None
    )
    if not pedido:
        await interaction.response.send_message(
            "Não encontrei um pedido seu aguardando comprovante neste canal.",
            ephemeral=True
        )
        return

    nome = Path(arquivo.filename).name
    tipo = (arquivo.content_type or "").lower()
    extensao = Path(nome).suffix.lower()
    if not (
        tipo.startswith("image/")
        or tipo == "application/pdf"
        or extensao in {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
    ):
        await interaction.response.send_message(
            "Formato inválido. Envie PNG, JPG, WEBP ou PDF.", ephemeral=True
        )
        return
    if arquivo.size > 10 * 1024 * 1024:
        await interaction.response.send_message(
            "O arquivo deve ter até 10 MB.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        dados = await arquivo.read(use_cached=True)
        if not dados or len(dados) > 10 * 1024 * 1024:
            await interaction.followup.send(
                "Não consegui ler o arquivo ou ele ultrapassa 10 MB.", ephemeral=True
            )
            return

        canal_admin = (
            interaction.guild.get_channel(int(pedido.get("admin_channel_id", 0)))
            if pedido.get("admin_channel_id") else None
        )
        if canal_admin is None:
            canal_admin = await obter_canal_confirmacao(interaction.guild)

        nome_anexo = nome[:180]
        await anexar_comprovantes_ao_pedido(
            pedido,
            canal_admin,
            [discord.File(io.BytesIO(dados), filename=nome_anexo)],
            [nome_anexo]
        )
        save_json(PIX_ORDERS_FILE, pix_orders)
        await interaction.followup.send(
            "✅ Comprovante enviado para conferência. Ele não foi publicado no chat.",
            ephemeral=True
        )
    except Exception as exc:
        log.exception("Falha ao encaminhar comprovante do pedido %s", pedido.get("txid"))
        await interaction.followup.send(
            "Não consegui encaminhar o arquivo. Tente novamente ou avise o administrador.",
            ephemeral=True
        )

@bot.listen("on_message")
async def receber_comprovante_pix(message: discord.Message):
    """Encaminha comprovantes do comprador para o canal administrativo de confirmação."""
    if message.author.bot or message.guild is None or not message.attachments:
        return
    pedido = next(
        (
            item for item in pix_orders.values()
            if str(item.get("ticket_channel_id")) == str(message.channel.id)
            and str(item.get("discord_id")) == str(message.author.id)
            and item.get("status") == "aguardando_confirmacao_manual"
        ),
        None
    )
    if not pedido:
        return
    arquivos_validos = []
    for anexo in message.attachments[:5]:
        nome = Path(anexo.filename).name
        tipo = (anexo.content_type or "").lower()
        extensao = Path(nome).suffix.lower()
        formato_valido = (
            extensao in {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
            or tipo in {"image/png", "image/jpeg", "image/webp", "application/pdf"}
        )
        if formato_valido and anexo.size <= 10 * 1024 * 1024:
            try:
                dados = await anexo.read(use_cached=True)
                if dados and len(dados) <= 10 * 1024 * 1024:
                    arquivos_validos.append(discord.File(io.BytesIO(dados), filename=nome[:180]))
            except Exception:
                log.exception("Falha ao ler comprovante do pedido %s", pedido.get("txid"))
    if not arquivos_validos:
        try:
            await message.channel.send(
                f"{message.author.mention} Não consegui ler o arquivo. Envie uma imagem PNG/JPG/WEBP ou PDF de até 10 MB."
            )
        except discord.HTTPException:
            pass
        return
    guild = message.guild
    canal_admin = guild.get_channel(int(pedido.get("admin_channel_id", 0))) if pedido.get("admin_channel_id") else None
    if canal_admin is None:
        try:
            canal_admin = await obter_canal_confirmacao(guild)
        except Exception:
            log.exception("Não consegui localizar o canal de comprovantes do pedido %s", pedido.get("txid"))
            return
    try:
        nomes_arquivos = [arquivo.filename for arquivo in arquivos_validos]
        await anexar_comprovantes_ao_pedido(
            pedido,
            canal_admin,
            arquivos_validos,
            nomes_arquivos
        )
        save_json(PIX_ORDERS_FILE, pix_orders)
        await message.reply("✅ Comprovante encaminhado para **Confirmar pagamentos**. Aguarde a conferência manual.", mention_author=False)
    except Exception:
        log.exception("Falha ao encaminhar comprovante do pedido %s", pedido.get("txid"))
        for arquivo in arquivos_validos:
            try:
                arquivo.close()
            except Exception:
                pass

@bot.event
async def on_app_command_error(interaction, error):
    # Registra todos os erros, inclusive os de usuários comuns, para evitar falhas silenciosas.
    log.error(
        "Erro em comando slash (%s; usuário=%s/%s): %s",
        getattr(getattr(interaction, "command", None), "qualified_name", "desconhecido"),
        getattr(interaction, "user", None),
        getattr(getattr(interaction, "user", None), "id", "desconhecido"),
        error,
        exc_info=(type(error), error, error.__traceback__)
    )
    try:
        mensagem = (
            "Ocorreu um erro ao executar o comando. O administrador foi avisado."
            if is_admin(interaction)
            else "Não consegui concluir esse comando. Tente novamente em instantes."
        )
        if interaction.response.is_done():
            await interaction.followup.send(mensagem, ephemeral=True)
        else:
            await interaction.response.send_message(mensagem, ephemeral=True)
    except Exception:
        log.exception("Não foi possível responder à interação que falhou.")

    if is_admin(interaction):
        await notify_admin(f"Erro em comando slash: {type(error).__name__}: {error}")

@bot.tree.command(name="criar-sites", description="Publicar o painel de sites neste canal")
async def criar_sites(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador pode publicar este painel.", ephemeral=True
        )
        return

    if interaction.guild is None or not isinstance(interaction.channel, discord.abc.Messageable):
        await interaction.response.send_message(
            "Use este comando em um canal do servidor.", ephemeral=True
        )
        return

    # Reformulação baseada nos templates DWEEB de diretório e cartões de produto:
    # texto editorial mais completo, respiro entre áreas e identidade própria para cada site.
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(content=(
            "# 🖥️ PORTAL C.C.\n"
            "**Projetos, ferramentas e recursos da comunidade em um só lugar.**\n\n"
            "Esta página reúne os sites ligados à C.C. Cada projeto tem uma proposta diferente: "
            "explorar materiais para produção musical, encontrar oportunidades ou conhecer os "
            "produtos e serviços desenvolvidos pela comunidade. Escolha o destino que combina "
            "com o que você procura e entre diretamente pela plataforma."
        )),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(content=(
            "## 🎚️ PACKy — PRODUÇÃO MUSICAL\n\n"
            "Um espaço voltado para quem está construindo seu próprio som. Explore packs e "
            "conteúdos pensados para acompanhar o processo de criação, descobrir novas referências "
            "e ampliar as possibilidades dentro do seu projeto musical."
        )),
        discord.ui.Section(
            discord.ui.TextDisplay(content=(
                "**Entre na PACKy** para conhecer os materiais disponíveis e encontrar recursos "
                "que façam sentido para a sua produção."
            )),
            accessory=discord.ui.Button(
                label="Conhecer a PACKy",
                emoji="🎧",
                style=discord.ButtonStyle.link,
                url="https://packy.lovable.app"
            )
        ),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(content=(
            "## 🧭 GARIMPO — DESCOBERTAS E OPORTUNIDADES\n\n"
            "O Garimpo é o ponto de partida para quem gosta de pesquisar, comparar e encontrar "
            "coisas que merecem atenção. Acesse o perfil da plataforma para conferir o projeto "
            "e explorar as oportunidades reunidas por lá."
        )),
        discord.ui.Section(
            discord.ui.TextDisplay(content=(
                "**Acesse o Garimpo** e veja o que pode valer a pena descobrir."
            )),
            accessory=discord.ui.Button(
                label="Explorar o Garimpo",
                emoji="🔎",
                style=discord.ButtonStyle.link,
                url="https://garimoh.lovable.app/perfil"
            )
        ),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(content=(
            "## 🛍️ LOJA CC — PRODUTOS E SERVIÇOS\n\n"
            "A loja reúne opções disponibilizadas pela comunidade C.C. É o lugar para conhecer "
            "o que está à venda, consultar os serviços oferecidos e encontrar algo que atenda "
            "ao que você precisa, sem precisar procurar as informações em vários canais."
        )),
        discord.ui.Section(
            discord.ui.TextDisplay(content=(
                "**Visite a Loja CC** para conferir o catálogo e os detalhes de cada opção."
            )),
            accessory=discord.ui.Button(
                label="Ver a loja",
                emoji="🛒",
                style=discord.ButtonStyle.link,
                url="https://ccloja.erbysite.com/"
            )
        )
    ))

    try:
        await interaction.channel.send(
            view=view,
            allowed_mentions=discord.AllowedMentions.none()
        )
        await interaction.response.send_message(
            "✅ Nova versão do painel **Sites** publicada neste canal. Nenhuma mensagem existente foi alterada.",
            ephemeral=True
        )
        log.info("Painel Sites publicado: canal=%s por=%s", interaction.channel.id, interaction.user.id)
    except discord.HTTPException as exc:
        log.exception("Falha ao publicar o painel Sites")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"❌ Não consegui publicar o painel ({type(exc).__name__}). "
                "Confira as permissões do bot e a versão do discord.py.",
                ephemeral=True
            )


@bot.tree.command(name="criar-sobre-nos", description="Publicar o painel Sobre Nós neste canal")
async def criar_sobre_nos(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador pode publicar este painel.", ephemeral=True
        )
        return

    if interaction.guild is None or not isinstance(interaction.channel, discord.abc.Messageable):
        await interaction.response.send_message(
            "Use este comando em um canal do servidor.", ephemeral=True
        )
        return

    # Inspirado no DWEEB discord-welcome-message.json:
    # cabeçalho, conteúdo de boas-vindas, divisor, links em botões e rodapé.
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(content=(
            "# 👥 SOBRE NÓS\n"
            "**Bem-vindo(a) à comunidade C.C.**\n"
            "-# UM ESPAÇO PARA CRIAR, APRENDER E EVOLUIR NO FUNK"
        )),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(content=(
            "Percebemos a falta de união na comunidade e a falta de recursos técnicos "
            "para quem quer criar, aprender e evoluir. Por isso, criamos este servidor "
            "como uma porta de apoio para a cena do funk.\n\n"
            "Aqui você encontra **venda de beats, ensinamentos, beats e packs gratuitos, "
            "campeonatos, eventos, serviços e muito mais**. Também contamos com parcerias "
            "com sites de Drum Kits para trazer ainda mais recursos aos produtores.\n\n"
            "-# Tudo foi pensado para ser simples e intuitivo. Esperamos que o servidor "
            "ajude você a criar, aprender, compartilhar e evoluir dentro do funk."
        )),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(content=(
            "### 🔗 Acompanhe a comunidade\n"
            "**Grupo no WhatsApp** — converse com a comunidade, divulgue seus trabalhos, "
            "conheça o projeto e acompanhe oportunidades.\n"
            "**Instagram** — confira anúncios, promoções, cupons e ofertas exclusivas."
        )),
        discord.ui.ActionRow(
            discord.ui.Button(
                label="Entrar no grupo",
                emoji="💬",
                style=discord.ButtonStyle.link,
                url="https://chat.whatsapp.com/JQ8hP8vys4QG4DB54wSi5E?s=cl&p=a&mlu=4&ilr=4"
            ),
            discord.ui.Button(
                label="Seguir no Instagram",
                emoji="📸",
                style=discord.ButtonStyle.link,
                url="https://www.instagram.com/ccloja.ig?dlrf=NGNmejZ6MWYyOHFx"
            )
        ),
    ))
    try:
        await interaction.channel.send(
            view=view,
            allowed_mentions=discord.AllowedMentions.none()
        )
        await interaction.response.send_message(
            "✅ Painel **Sobre Nós** publicado neste canal. Nenhuma mensagem existente foi alterada.",
            ephemeral=True
        )
        log.info("Painel Sobre Nós publicado: canal=%s por=%s", interaction.channel.id, interaction.user.id)
    except discord.HTTPException as exc:
        log.exception("Falha ao publicar o painel Sobre Nós")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"❌ Não consegui publicar o painel ({type(exc).__name__}). "
                "Confira as permissões do bot e a versão do discord.py.",
                ephemeral=True
            )


@bot.tree.command(name="criar-regras", description="Publicar uma nova mensagem de regras sem alterar a existente")
@app_commands.describe(canal="Canal onde a nova mensagem de regras será publicada")
async def criar_regras(interaction: discord.Interaction, canal: discord.TextChannel):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador pode publicar as regras.", ephemeral=True
        )
        return

    # Estética baseada no template DWEEB discord-server-rules-template.json:
    # título, divisor, regras em bloco, divisor e aviso final; usa Components V2.
    regras = (
        "**01 · Respeito**\n"
        "Racismo, preconceito, assédio, ameaças e discriminação não serão tolerados. "
        "Racismo e preconceito geram **banimento permanente**.\n\n"
        "**02 · Conteúdo proibido**\n"
        "É proibido divulgar bets/apostas, pornografia, conteúdo sexual explícito/adulto, "
        "golpes, links maliciosos ou conteúdo ilegal. A divulgação de conteúdo adulto, "
        "racista ou preconceituoso gera **banimento permanente**.\n\n"
        "**03 · Divulgação**\n"
        "O canal de divulgações é exclusivo para **beats**. Não divulgue lojas, grupos, "
        "servidores, serviços ou produtos fora dos espaços autorizados. Spam pode ser removido.\n\n"
        "**04 · Compras**\n"
        "Antes de pagar, leia **como funcionam as vendas**, confira produto, preço e condições. "
        "O pagamento é conferido manualmente; comprovante não significa aprovação automática. "
        "Não abra pedidos duplicados.\n\n"
        "**05 · Golpes**\n"
        "Tentativa de golpe, fraude, comprovante falso ou tentativa de enganar clientes/equipe "
        "gera **banimento imediato**. Nunca compartilhe senhas ou dados bancários completos.\n\n"
        "**06 · Suporte**\n"
        "Se houver problema com compra, entrega ou acesso, abra um ticket no **Suporte Dedicado**. "
        "A equipe busca responder em até **24 horas** e também oferece contato por WhatsApp.\n\n"
        "**07 · Materiais**\n"
        "Respeite as condições de cada produto e material gratuito. Não revenda ou redistribua "
        "conteúdo contrariando as condições informadas.\n\n"
        "**08 · Moderação**\n"
        "As regras valem para todos, inclusive clientes. Cada compra ajuda a manter o projeto ativo, "
        "mas não dá privilégios nem muda as regras. A equipe pode remover conteúdo e aplicar "
        "advertência, restrição ou banimento conforme a gravidade.\n\n"
        "**09 · Segurança nas vendas**\n"
        "Mantenha a conversa no canal privado do pedido. Se algo parecer estranho, pare e fale com "
        "o suporte antes de continuar."
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(content=(
            "# 📜 REGRAS DA COMUNIDADE\n"
            "**Uma comunidade criativa, segura e transparente.**\n"
            "-# LEIA ANTES DE PARTICIPAR OU COMPRAR"
        )),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(content=regras),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(content=(
            "> Ao permanecer no servidor, você concorda em seguir estas regras.\n"
            "> A equipe pode aplicar advertência, restrição ou banimento conforme a gravidade."
        )),
        accent_color=5763719
    ))
    view.add_item(discord.ui.TextDisplay(
        content="-# C.C ©  •  COMUNIDADE E PRODUÇÃO MUSICAL"
    ))

    try:
        await canal.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        await interaction.response.send_message(
            f"✅ Nova mensagem de regras publicada em {canal.mention}. "
            "A mensagem antiga não foi alterada.", ephemeral=True
        )
        log.info("Nova mensagem de regras Components V2 publicada: canal=%s por=%s", canal.id, interaction.user.id)
    except discord.HTTPException as exc:
        log.exception("Falha ao publicar nova mensagem de regras")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"❌ Não consegui publicar as novas regras ({type(exc).__name__}). "
                "Confira as permissões do bot no canal.", ephemeral=True
            )


@bot.tree.command(name="atualizar-painel", description="Atualizar um painel existente em qualquer canal")
@app_commands.describe(
    canal="Canal onde a mensagem já está publicada",
    mensagem_id="ID da mensagem do painel que será atualizada",
    tipo="Modelo de painel que será aplicado à mensagem existente"
)
@app_commands.choices(tipo=[
    app_commands.Choice(name="Gerador de Samples", value="samples"),
    app_commands.Choice(name="Pack Personalizado", value="pack"),
    app_commands.Choice(name="Arrumador", value="arrumador"),
    app_commands.Choice(name="Acapellas", value="acapellas"),
    app_commands.Choice(name="Parcerias", value="parcerias"),
    app_commands.Choice(name="Trampar Conosco", value="trampar"),
])
async def atualizar_painel(
    interaction: discord.Interaction,
    canal: discord.TextChannel | discord.Thread,
    mensagem_id: str,
    tipo: app_commands.Choice[str]
):
    """Atualiza mensagens em canais de texto e threads; nunca duplica a mensagem."""
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador autorizado pode atualizar painéis.", ephemeral=True
        )
        return

    try:
        alvo_id = int(mensagem_id.strip())
        if alvo_id <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await interaction.response.send_message(
            "ID de mensagem inválido. Copie o ID numérico da mensagem do Discord.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        mensagem = await canal.fetch_message(alvo_id)
        if mensagem.author.id != bot.user.id:
            await interaction.edit_original_response(
                content="❌ Só posso atualizar mensagens enviadas pelo próprio CC BOT."
            )
            return

        tipos = {
            "samples": (GeradorSamplesView, True),
            "pack": (PackPersonalizadoView, True),
            "arrumador": (ArrumadorPainelView, True),
            "acapellas": (AcapellasActivityView, False),
            "parcerias": (ParceriasView, True),
            "trampar": (TramparConoscoView, True),
        }
        fabrica, components_v2 = tipos[tipo.value]
        nova_view = fabrica()
        if components_v2:
            # Components V2 é o próprio painel; o Discord não permite misturá-lo com embeds.
            await mensagem.edit(content=None, embeds=[], attachments=[], view=nova_view)
        else:
            # Outros painéis tradicionais mantêm seu embed/conteúdo; só atualizamos os botões.
            await mensagem.edit(view=nova_view)

        await interaction.edit_original_response(
            content=f"✅ Painel **{tipo.name}** atualizado na mensagem existente em {canal.mention}. Nenhuma cópia foi criada."
        )
        log.info(
            "Painel atualizado manualmente: tipo=%s canal=%s mensagem=%s por=%s",
            tipo.value, canal.id, mensagem.id, interaction.user.id
        )
    except discord.NotFound:
        await interaction.edit_original_response(
            content="❌ Não encontrei essa mensagem nesse canal. Confira o canal e o ID."
        )
    except discord.Forbidden:
        await interaction.edit_original_response(
            content="❌ O bot não tem permissão para ler o histórico ou editar mensagens nesse canal."
        )
    except Exception as exc:
        log.exception("Falha no comando /atualizar-painel")
        detalhe = f" (HTTP {exc.status}, código {exc.code})" if isinstance(exc, discord.HTTPException) else ""
        await interaction.edit_original_response(
            content=f"❌ Não consegui atualizar o painel ({type(exc).__name__}{detalhe}). Confira os logs do bot."
        )


@bot.tree.command(name="atualizar-paineis", description="Atualizar automaticamente os painéis reconhecidos em todos os canais")
async def atualizar_paineis(interaction: discord.Interaction):
    """Varre os canais e threads acessíveis e atualiza painéis do bot sem exigir IDs manuais."""
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador autorizado pode atualizar painéis.", ephemeral=True
        )
        return
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Use este comando dentro do servidor.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    atualizados = 0
    ignorados = 0
    falhas = 0
    vistos = set()
    comprovante_panel_atualizado = False
    status_comprovante = "ℹ️ Painel informativo de comprovante não encontrado; nenhuma mensagem nova foi criada."
    status_pack = "❌ Não foi possível restaurar o painel."

    # Edita a mensagem original e informa o erro real; não cria cópia.
    try:
        canal_pack = bot.get_channel(PACK_PANEL_CHANNEL_ID) or await bot.fetch_channel(PACK_PANEL_CHANNEL_ID)
        if not isinstance(canal_pack, (discord.TextChannel, discord.Thread)):
            raise TypeError(f"O canal {PACK_PANEL_CHANNEL_ID} não é um canal de texto ou thread.")
        mensagem_pack = await canal_pack.fetch_message(PACK_PANEL_MESSAGE_ID)
        if not bot.user or mensagem_pack.author.id != bot.user.id:
            raise PermissionError("A mensagem indicada não foi enviada pelo CC BOT.")
        await mensagem_pack.edit(content=None, embeds=[], attachments=[], view=PackPersonalizadoView())
        vistos.add(mensagem_pack.id)
        atualizados += 1
        status_pack = "✅ Pack Personalizado restaurado na mensagem original."
        log.info("Pack Personalizado restaurado diretamente: canal=%s mensagem=%s", canal_pack.id, mensagem_pack.id)
    except Exception as exc:
        falhas += 1
        status_pack = f"❌ Pack Personalizado: {type(exc).__name__}: {str(exc)[:250]}"
        log.exception("Falha na restauração direta do Pack Personalizado (canal=%s mensagem=%s)", PACK_PANEL_CHANNEL_ID, PACK_PANEL_MESSAGE_ID)

    def texto_mensagem(msg):
        # Serializa componentes por dicionário quando possível; isso também captura
        # TextDisplay/Section de Components V2 que não expõem todos os campos como atributos.
        partes = [msg.content or ""]
        for emb in msg.embeds:
            partes.extend([emb.title or "", emb.description or ""])
            if emb.footer:
                partes.append(emb.footer.text or "")
        try:
            partes.append(json.dumps(
                [componente.to_dict() for componente in msg.components],
                ensure_ascii=False,
                default=str
            ))
        except Exception:
            partes.append(str(msg.components))
        return " ".join(partes).casefold()

    def detectar(texto, mensagem, canal):
        # Recupera o painel conhecido mesmo se ele foi reduzido a texto puro e perdeu os botões.
        if mensagem.id == PACK_PANEL_MESSAGE_ID:
            return "Pack Personalizado", PackPersonalizadoView, True

        # Nunca tratar pedidos de pagamento/comprovantes como painéis, mesmo se os
        # detalhes do comprador mencionarem um produto ou serviço do servidor.
        for embed in mensagem.embeds:
            titulo = (embed.title or "").casefold()
            descricao = (embed.description or "").casefold()
            campos = " ".join((campo.name or "").casefold() for campo in embed.fields)
            if (
                "pagamento pendente" in titulo
                or "pagamento pendente" in descricao
                or "comprovante(s) recebido(s)" in campos
                or "referência técnica:" in (embed.footer.text or "").casefold()
            ):
                return None

        # Atualiza uma mensagem informativa em canal dedicado a comprovantes.
        # Os embeds de pagamentos pendentes são excluídos acima.
        if "comprovante" in getattr(canal, "name", "").casefold():
            if mensagem.content or mensagem.embeds or mensagem.components:
                return "Comprovante", ComprovanteInfoView, False

        # Use IDs de componentes ou títulos exatos: texto de pedidos/comprovantes
        # pode mencionar produtos e nunca deve ser confundido com um painel.
        if "ccbot:pack:v2:basico" in texto:
            return "Pack Personalizado", PackPersonalizadoView, True
        if "gerador de samples" in texto and ("painel atualizado" in texto or "ccbot" in texto):
            return "Gerador de Samples", GeradorSamplesView, True
        if "trampar conosco" in texto or "central de oportunidades" in texto:
            return "Trampar Conosco", TramparConoscoView, True
        if "conecte com o c.c" in texto or "apresentar meu grupo" in texto:
            return "Parcerias", ParceriasView, True

        titulos = {(embed.title or "").strip().casefold() for embed in mensagem.embeds}
        # O ID do botão permite recuperar o painel mesmo quando seu embed já foi apagado.
        if "ccbot:arrumador_mandar" in texto or titulos.intersection({"🧰 arrumador de arquivos", "🧰 arrumador de projetos", "🧩 arrumador"}):
            return "Arrumador", ArrumadorPainelView, True
        if titulos.intersection({"🎤 acapellas de mcs famosos", "busca de acapellas"}):
            return "Acapellas", AcapellasActivityView, False
        return None

    canais = list(guild.text_channels)
    canais.extend([t for t in guild.threads if t not in canais])
    for canal in canais:
        try:
            async for mensagem in canal.history(limit=200):
                if mensagem.id in vistos or not bot.user or mensagem.author.id != bot.user.id:
                    continue
                # Mensagens sem componentes normalmente são ignoradas, exceto o ID fixo
                # do painel de Pack, que precisa ser recuperado se perdeu o layout/botão.
                canal_com_comprovante = "comprovante" in getattr(canal, "name", "").casefold()
                if not mensagem.components and mensagem.id != PACK_PANEL_MESSAGE_ID and not canal_com_comprovante:
                    continue
                if canal_com_comprovante and comprovante_panel_atualizado:
                    continue
                tipo_detectado = detectar(texto_mensagem(mensagem), mensagem, canal)
                if not tipo_detectado:
                    ignorados += 1
                    continue
                nome, fabrica, components_v2 = tipo_detectado
                try:
                    nova_view = fabrica()
                    if components_v2:
                        # Components V2 contém o layout completo do painel.
                        await mensagem.edit(content=None, embeds=[], attachments=[], view=nova_view)
                    elif nome == "Comprovante":
                        await mensagem.edit(
                            content=None,
                            embed=criar_embed_comprovante_info(),
                            attachments=[],
                            view=nova_view
                        )
                        comprovante_panel_atualizado = True
                        status_comprovante = "✅ Painel de Comprovante restaurado na mensagem existente."
                    else:
                        await mensagem.edit(view=nova_view)
                    vistos.add(mensagem.id)
                    atualizados += 1
                    log.info("Painel atualizado automaticamente: tipo=%s canal=%s mensagem=%s", nome, canal.id, mensagem.id)
                except discord.NotFound:
                    continue
                except discord.HTTPException:
                    falhas += 1
                    log.exception("Falha ao atualizar painel automaticamente: %s (%s)", nome, mensagem.id)
        except (discord.Forbidden, discord.HTTPException):
            falhas += 1
            log.warning("Não foi possível examinar o canal %s durante atualização automática.", canal.id)

    await interaction.edit_original_response(
        content=(
            "✅ **Varredura concluída**\n"
            f"Painéis atualizados: **{atualizados}**\n"
            f"Mensagens ignoradas: **{ignorados}**\n"
            f"Falhas ou canais sem acesso: **{falhas}**\n\n"
            f"{status_pack}\n"
            f"{status_comprovante}\n\n"
            "Nenhuma mensagem nova foi criada. "
            "A busca verificou até 200 mensagens recentes por canal. "
            "Se algum painel ficou de fora, ainda não existe um modelo automático seguro para ele."
        )
    )


@bot.tree.command(name="parcerias", description="Criar ou atualizar a aba de Parcerias automaticamente")
async def comando_parcerias(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador autorizado pode gerenciar a aba de Parcerias.", ephemeral=True
        )
        return
    if interaction.guild is None or interaction.guild.id != 1561513043886743562:
        await interaction.response.send_message(
            "Use este comando dentro do servidor oficial do CC BOT.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        canal = bot.get_channel(PARTNERSHIPS_CHANNEL_ID)
        if canal is None:
            canal = await bot.fetch_channel(PARTNERSHIPS_CHANNEL_ID)
        if not isinstance(canal, discord.TextChannel):
            await interaction.edit_original_response(
                content="❌ O canal configurado para Parcerias não é um canal de texto."
            )
            return

        registros = settings.setdefault("community_panels", {})
        registro = registros.get("Parcerias", {})
        mensagem = None
        mensagem_id = registro.get("message_id") if isinstance(registro, dict) else None
        if not mensagem_id:
            mensagem_id = 1562596483688300545

        # Primeiro tenta os IDs já conhecidos.
        try:
            mensagem = await canal.fetch_message(int(mensagem_id))
            if mensagem.author.id != bot.user.id:
                mensagem = None
        except (discord.NotFound, discord.HTTPException, ValueError, TypeError):
            mensagem = None

        # Se o ID antigo estiver desatualizado, procura a aba existente antes de criar outra.
        if mensagem is None:
            async for candidata in canal.history(limit=200):
                if candidata.author.id != bot.user.id:
                    continue
                texto = " ".join([
                    candidata.content or "",
                    *[(e.title or "") + " " + (e.description or "") for e in candidata.embeds],
                    str([c.to_dict() for c in candidata.components])
                ]).casefold()
                if "conecte com o c.c" in texto or "apresentar meu grupo" in texto or "parcerias • colaboração" in texto:
                    mensagem = candidata
                    break

        if mensagem is None:
            mensagem = await canal.send(view=ParceriasView(), allowed_mentions=discord.AllowedMentions.none())
            resultado = "✅ Aba de Parcerias criada no canal configurado."
        else:
            await mensagem.edit(
                content=None,
                embeds=[],
                attachments=[],
                view=ParceriasView(),
                allowed_mentions=discord.AllowedMentions.none()
            )
            resultado = "✅ Aba de Parcerias atualizada na mensagem existente. Nenhuma cópia foi criada."

        registros["Parcerias"] = {"message_id": mensagem.id}
        save_json(DATA_FILE, settings)
        await interaction.edit_original_response(
            content=f"{resultado}\nCanal: {canal.mention}\nID da mensagem salvo automaticamente."
        )
        log.info("Aba Parcerias processada: canal=%s mensagem=%s por=%s", canal.id, mensagem.id, interaction.user.id)
    except Exception as exc:
        log.exception("Falha ao criar/atualizar aba de Parcerias")
        detalhe = f" (HTTP {exc.status}, código {exc.code})" if isinstance(exc, discord.HTTPException) else ""
        await interaction.edit_original_response(
            content=f"❌ Não consegui preparar a aba de Parcerias ({type(exc).__name__}{detalhe}). Confira os logs."
        )


@bot.tree.command(name="diagnostico", description="Analisar os canais do servidor e enviar um relatório")
async def diagnostico(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador pode executar este diagnóstico.",
            ephemeral=True
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "Use este comando dentro do servidor.",
            ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True, ephemeral=False)
    guild = interaction.guild
    linhas = [
        f"DIAGNÓSTICO DO SERVIDOR: {guild.name}",
        f"Servidor ID: {guild.id}",
        f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
        "",
        "CANAIS DE TEXTO"
    ]
    com_mensagens = 0
    vazios = 0
    sem_acesso = 0

    for canal in sorted(guild.text_channels, key=lambda c: ((c.category.name if c.category else ""), c.position, c.name)):
        categoria = canal.category.name if canal.category else "Sem categoria"
        try:
            ultima = None
            async for mensagem in canal.history(limit=1):
                ultima = mensagem
            if ultima is None:
                status = "SEM MENSAGENS VISÍVEIS"
                vazios += 1
            else:
                status = f"TEM MENSAGENS (última: {ultima.created_at.strftime('%d/%m/%Y %H:%M UTC')})"
                com_mensagens += 1
        except discord.Forbidden:
            status = "SEM PERMISSÃO PARA LER HISTÓRICO"
            sem_acesso += 1
        except discord.HTTPException as exc:
            status = f"ERRO DO DISCORD ({exc.status})"
            sem_acesso += 1
        linhas.append(f"[{categoria}] #{canal.name} | ID {canal.id} | {status}")

    linhas.extend([
        "",
        "RESUMO",
        f"Canais de texto analisados: {len(guild.text_channels)}",
        f"Com mensagens visíveis: {com_mensagens}",
        f"Sem mensagens visíveis: {vazios}",
        f"Sem acesso/erro: {sem_acesso}",
        "",
        "Nota: 'sem mensagens visíveis' significa que o bot não encontrou mensagem no histórico acessível."
    ])
    relatorio = "\n".join(linhas)
    arquivo = discord.File(
        io.BytesIO(relatorio.encode("utf-8")),
        filename="diagnostico_servidor.txt"
    )
    await interaction.followup.send(
        content=(
            "📋 **Diagnóstico concluído**\n"
            f"• Canais com mensagens: **{com_mensagens}**\n"
            f"• Sem mensagens visíveis: **{vazios}**\n"
            f"• Sem acesso/erro: **{sem_acesso}**\n"
            "O relatório completo está anexado."
        ),
        file=arquivo
    )

# CCBOT_PROCESS_LOCK: impede múltiplas instâncias de iniciar sessões Discord concorrentes.
# O descritor fica aberto durante toda a execução; o kernel libera o lock ao encerrar o processo.
_lock_path = CONFIG / "ccbot-process.lock"
_lock_handle = _lock_path.open("a+", encoding="utf-8")
try:
    fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("Outra instância do CC BOT já está ativa; esta instância será encerrada.")

if not TOKEN_FILE.is_file():
    raise SystemExit(
        f"Token não encontrado em {TOKEN_FILE}. Crie o arquivo com o token do bot."
    )

TOKEN = TOKEN_FILE.read_text(encoding="utf-8").strip()
if not TOKEN:
    raise SystemExit("O arquivo token.txt está vazio.")

bot.run(TOKEN)