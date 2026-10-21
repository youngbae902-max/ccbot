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

BASE = Path.home() / "ccbot-repo"
CONFIG = BASE / "config"
DATA_FILE = CONFIG / "config.json"
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
    "notice_channel": DEFAULT_NOTICE_CHANNEL,
    "sales_count": 0
})

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

        embed = discord.Embed(
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

        instrucoes = discord.Embed(
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
                view=None
            )
            return

        preco = f"R$ {valor_numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        nome_beat = self.nome.value.strip()
        descricao_beat = self.descricao.value.strip()
        estilo_beat = self.estilo.value.strip()

        embed = discord.Embed(
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
                    f"Acesse o canal privado {ticket.mention} para pagar. Identificador: {txid}. "
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
        embed = discord.Embed(
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
                "## <:tag:1561787923865665667> PACK PERSONALIZADO — R$ 50,00\n"
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
        basico = discord.ui.Button(label="PACK PERSONALIZADO", emoji="<:tag:1561787923865665667>", style=discord.ButtonStyle.primary, custom_id="ccbot:pack:v2:basico")
        basico.callback = self.abrir_basico
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(content=(
                "## <:box:1561761831587160140> PACK PERSONALIZADO\n"
                "### <:tag:1561787923865665667> R$ 50,00\n"
                "Informe nome, estilo e ideia do pack.\n"
                "**Adicionais:** organização (+ R$ 10), samples personalizadas (+ R$ 10) "
                "ou samples de outros packs (+ R$ 20)."
            )),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
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
PAYMENT_CATEGORY_NAME = "Mercado"
PAYMENT_ADMIN_CATEGORY_NAME = "Administração"
PAYMENT_ADMIN_CHANNEL_NAME = "confirmar-pagamentos"


def formatar_reais(valor):
    return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def extrair_txid_da_mensagem(message):
    if not message or not message.embeds:
        return None
    for field in message.embeds[0].fields:
        if field.name.casefold() in ("identificador", "identificador pix", "pedido"):
            candidato = re.sub(r"[^A-Za-z0-9]", "", field.value).upper()
            if candidato in pix_orders:
                return candidato
    return None


async def obter_canal_confirmacao(guild):
    canal_id = settings.get("payment_admin_channel_id")
    canal = guild.get_channel(int(canal_id)) if canal_id else None
    if isinstance(canal, discord.TextChannel):
        return canal
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
        categoria = await guild.create_category(
            PAYMENT_ADMIN_CATEGORY_NAME, overwrites=overwrites,
            reason="Configuração de confirmação manual de pagamentos"
        )
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


async def criar_pedido_pagamento(interaction, produto, valor, detalhes=None, tipo="digital"):
    if interaction.guild is None:
        raise ValueError("O pagamento precisa ser iniciado dentro do servidor.")
    chave = str(settings.get("pix_key", "")).strip()
    nome_recebedor = str(settings.get("pix_receiver_name", "")).strip()
    cidade = str(settings.get("pix_city", "")).strip()
    if not chave or not nome_recebedor or not cidade:
        raise ValueError("Pix não configurado. Use /pix configurar com chave, nome do recebedor e cidade.")

    txid = "CC" + uuid.uuid4().hex[:16].upper()
    user = interaction.user
    nome_usuario = str(getattr(user, "global_name", None) or user.name)
    payload = gerar_payload_pix(chave, nome_recebedor, cidade, float(valor), txid)
    imagem = qrcode.make(payload)
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    buffer.seek(0)

    guild = interaction.guild
    categoria_mercado = discord.utils.get(guild.categories, name=PAYMENT_CATEGORY_NAME)
    if categoria_mercado is None:
        categoria_mercado = await guild.create_category(
            PAYMENT_CATEGORY_NAME, reason="Canais privados de pagamento do Mercado"
        )
    admin = guild.get_member(ADMIN_ID)
    if admin is None:
        try:
            admin = await guild.fetch_member(ADMIN_ID)
        except discord.HTTPException:
            admin = None
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
        "product": str(produto)[:100], "amount": round(float(valor), 2),
        "details": str(detalhes or "")[:4000], "fulfillment_type": tipo,
        "status": "aguardando_confirmacao_manual",
        "created_at": datetime.now().isoformat(timespec="seconds")
    }
    pix_orders[txid] = registro
    save_json(PIX_ORDERS_FILE, pix_orders)

    embed = discord.Embed(
        title="Pagamento Pix pendente",
        description=(
            f"Produto: {produto}\nValor: {formatar_reais(valor)}\n"
            f"Comprador: {nome_usuario}\nID Discord: {user.id}\n"
            f"Identificador: {txid}\n\n"
            "Escaneie o QR Code ou copie o código Pix enviado. Após pagar, aguarde a conferência manual.\n"
            "O pagamento não libera o produto automaticamente."
        ),
        color=discord.Color.orange(), timestamp=discord.utils.utcnow()
    )
    embed.set_image(url="attachment://pix_cc.png")
    if detalhes:
        embed.add_field(name="Detalhes do pedido", value=str(detalhes)[:1024], inline=False)
    embed.set_footer(text=f"Pedido {txid} • Pagamento pendente")
    await ticket.send(
        content=payload, embed=embed,
        file=discord.File(buffer, filename="pix_cc.png")
    )

    try:
        canal_admin = await obter_canal_confirmacao(guild)
        aviso = discord.Embed(
            title="🧾 PAGAMENTO PENDENTE — CONFERÊNCIA MANUAL",
            description=(
                f"**Produto:** {produto}\n"
                f"**Valor:** {formatar_reais(valor)}\n"
                f"**Status:** AGUARDANDO CONFERÊNCIA"
            ),
            color=discord.Color.orange(), timestamp=discord.utils.utcnow()
        )
        # Dados do comprador em campos próprios para ficarem visíveis no comprovante administrativo.
        aviso.add_field(name="👤 Nome no Discord", value=f"{nome_usuario}\n{user.mention}", inline=True)
        aviso.add_field(name="🆔 ID DO USUÁRIO", value=f"`{user.id}`", inline=True)
        aviso.add_field(name="🔖 IDENTIFICADOR ÚNICO DO PEDIDO", value=f"`{txid}`", inline=False)
        aviso.add_field(name="Canal privado do pedido", value=ticket.mention, inline=False)
        if detalhes:
            aviso.add_field(name="Resumo do pedido", value=str(detalhes)[:1024], inline=False)
        await canal_admin.send(embed=aviso, view=ConfirmarPagamentoView())
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
        pedido["status"] = "pago_confirmado_manualmente" if aprovado else "cancelado_manualmente"
        pedido["processed_at"] = datetime.now().isoformat(timespec="seconds")
        pedido["processed_by"] = str(interaction.user.id)
        save_json(PIX_ORDERS_FILE, pix_orders)

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
                log.warning("Não foi possível enviar o resultado do pedido %s ao PV do usuário %s", txid, pedido.get("discord_id"))

        ticket_id = pedido.get("ticket_channel_id")
        if guild and ticket_id:
            ticket = guild.get_channel(int(ticket_id))
            if isinstance(ticket, discord.TextChannel):
                try:
                    if comprador:
                        await ticket.set_permissions(comprador, view_channel=False, send_messages=False)
                    prefixo = "pago-" if aprovado else "cancelado-"
                    await ticket.edit(
                        name=(prefixo + ticket.name.removeprefix("pagamento-"))[:100],
                        topic=f"Pedido {txid} | Status: {'confirmado manualmente' if aprovado else 'cancelado'}",
                        reason="Pedido processado manualmente pelo administrador"
                    )
                except discord.HTTPException:
                    log.exception("Falha ao fechar o canal privado do pedido %s", txid)

        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green() if aprovado else discord.Color.red()
            embed.description = (embed.description or "") + (
                "\n\nSTATUS FINAL: PAGAMENTO CONFIRMADO MANUALMENTE"
                if aprovado else "\n\nSTATUS FINAL: PEDIDO CANCELADO"
            )
            embed.set_footer(text=f"Identificador: {txid} • Processado por {interaction.user.id}")
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            await interaction.response.send_message(
                f"Pedido {txid}: {'confirmado' if aprovado else 'cancelado'}.", ephemeral=True
            )

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
            embed = discord.Embed(
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
        super().__init__(label="Adicionar emojis da pasta", emoji="😀", style=discord.ButtonStyle.success, custom_id="ccbot:painel_adicionar_emojis")

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        await adicionar_emojis.callback(interaction)


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
        self.add_item(AdicionarEmojisPainelButton())
        self.add_item(BackupPainelButton("termux"))
        self.add_item(BackupPainelButton("discord"))
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
                embed=discord.Embed(
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




# Sistema de ícones importados da pasta do Pinterest no Android.
PINTEREST_PINS_DIR = Path("/sdcard/Pictures/100PINT/Pins")
ICONS_CHANNEL_ID = 1561513290473934998
ICON_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ICON_POLL_SECONDS = 5

def icon_files():
    try:
        return sorted(
            (p for p in PINTEREST_PINS_DIR.iterdir()
             if p.is_file() and p.suffix.lower() in ICON_EXTENSIONS),
            key=lambda p: (p.stat().st_mtime, p.name.lower())
        )
    except (FileNotFoundError, PermissionError, OSError):
        return []

def icon_state():
    state = settings.setdefault("icon_system", {})
    state.setdefault("monitoring", False)
    state.setdefault("pending", None)
    state.setdefault("known_files", [])
    return state

async def publicar_album_icone(nome, paths):
    if not paths:
        raise ValueError("Nenhuma foto encontrada para publicar.")
    canal = bot.get_channel(ICONS_CHANNEL_ID)
    if canal is None:
        canal = await bot.fetch_channel(ICONS_CHANNEL_ID)
    if not isinstance(canal, discord.TextChannel):
        raise TypeError("O canal de ícones configurado não é um canal de texto.")

    enviados = 0
    for inicio in range(0, len(paths), 10):
        lote = paths[inicio:inicio + 10]
        arquivos = []
        try:
            for path in lote:
                arquivos.append(discord.File(str(path), filename=path.name))
            embed = discord.Embed(
                title=f"Icon - {nome}" if inicio == 0 else f"Icon - {nome} (continuação)",
                description=f"📸 {len(paths)} foto(s)" if inicio == 0 else None,
                color=discord.Color.from_rgb(0, 0, 0)
            )
            embed.set_footer(text="CC BOT • Icons")
            await canal.send(embed=embed, files=arquivos)
            enviados += len(lote)
        finally:
            for arquivo in arquivos:
                try:
                    arquivo.close()
                except Exception:
                    pass
    return enviados

class IconNomeQuantidadeModal(discord.ui.Modal, title="Cadastrar ícone"):
    nome = discord.ui.TextInput(
        label="Nome do ícone",
        placeholder="Ex.: brocasito",
        min_length=1,
        max_length=80
    )
    quantidade = discord.ui.TextInput(
        label="Quantidade de fotos",
        placeholder="Ex.: 10",
        min_length=1,
        max_length=4
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        try:
            quantidade = int(str(self.quantidade.value).strip())
            if not 1 <= quantidade <= 500:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Informe uma quantidade entre 1 e 500.", ephemeral=True
            )
            return

        state = icon_state()
        atuais = [str(p.resolve()) for p in icon_files()]
        state["pending"] = {
            "name": str(self.nome.value).strip(),
            "quantity": quantidade,
            "baseline": atuais,
            "started_at": datetime.now().isoformat(timespec="seconds")
        }
        state["known_files"] = list(set(state.get("known_files", [])) | set(atuais))
        save_json(DATA_FILE, settings)
        await interaction.response.send_message(
            f"Icon - {str(self.nome.value).strip()} cadastrado.\n"
            f"Agora baixe {quantidade} foto(s) do Pinterest para {PINTEREST_PINS_DIR}. "
            "Quando a quantidade for atingida, vou publicar o álbum no canal de ícones.",
            ephemeral=True
        )

class IconImportarModal(discord.ui.Modal, title="Importar fotos existentes"):
    nome = discord.ui.TextInput(
        label="Nome do ícone",
        placeholder="Ex.: brocasito",
        min_length=1,
        max_length=80
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        paths = icon_files()
        if not paths:
            await interaction.followup.send(
                f"Não encontrei fotos em {PINTEREST_PINS_DIR}. Confira a pasta e as permissões do Termux.",
                ephemeral=True
            )
            return
        try:
            total = await publicar_album_icone(str(self.nome.value).strip(), paths)
            state = icon_state()
            state["known_files"] = list(set(state.get("known_files", [])) | {str(p.resolve()) for p in paths})
            save_json(DATA_FILE, settings)
            await interaction.followup.send(
                f"Icon - {str(self.nome.value).strip()} publicado com {total} foto(s).",
                ephemeral=True
            )
        except Exception as exc:
            log.exception("Falha ao importar fotos existentes como ícone")
            await interaction.followup.send(
                f"Não consegui publicar as fotos ({type(exc).__name__}). Confira as permissões do bot no canal.",
                ephemeral=True
            )

class IconMonitoramentoButton(discord.ui.Button):
    def __init__(self):
        state = icon_state()
        ativo = bool(state.get("monitoring"))
        super().__init__(
            label="Desativar monitoramento" if ativo else "Ativar monitoramento",
            emoji="🟢" if ativo else "⚪",
            style=discord.ButtonStyle.success if ativo else discord.ButtonStyle.secondary,
            custom_id="ccbot:icons_toggle_monitor"
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        state = icon_state()
        state["monitoring"] = not bool(state.get("monitoring"))
        save_json(DATA_FILE, settings)
        await interaction.response.edit_message(
            embed=criar_embed_painel_icones(),
            view=PainelIconesView()
        )

class IconAdicionarButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Cadastrar ícone e quantidade",
            emoji="➕",
            style=discord.ButtonStyle.primary,
            custom_id="ccbot:icons_add"
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        await interaction.response.send_modal(IconNomeQuantidadeModal())

class IconImportarButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Importar fotos da pasta",
            emoji="🖼️",
            style=discord.ButtonStyle.primary,
            custom_id="ccbot:icons_import"
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return
        await interaction.response.send_modal(IconImportarModal())

class PainelIconesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(IconMonitoramentoButton())
        self.add_item(IconAdicionarButton())
        self.add_item(IconImportarButton())
        self.add_item(VoltarPainelButton())

def criar_embed_painel_icones():
    state = icon_state()
    pendente = state.get("pending")
    status = "Ativo" if state.get("monitoring") else "Desativado"
    aguardando = (
        f"Ícone em andamento: {pendente['name']} · {pendente['quantity']} fotos"
        if pendente else "Ícone em andamento: nenhum"
    )
    embed = discord.Embed(
        title="Central de Ícones",
        description=(
            f"Monitoramento: {status}\n{aguardando}\n\n"
            f"Pasta: {PINTEREST_PINS_DIR}\n"
            f"Canal de publicação: <#{ICONS_CHANNEL_ID}>\n\n"
            "1. Ative o monitoramento.\n"
            "2. Cadastre o nome e a quantidade.\n"
            "3. Baixe as fotos do Pinterest para a pasta Pins.\n"
            "4. Ao atingir a quantidade, o bot publica o álbum automaticamente.\n\n"
            "A opção de importar publica as fotos que já estão na pasta."
        ),
        color=discord.Color.from_rgb(190, 45, 75)
    )
    embed.set_footer(text="CC BOT • Icons")
    return embed

@tasks.loop(seconds=ICON_POLL_SECONDS)
async def monitorar_pasta_icones():
    state = icon_state()
    if not state.get("monitoring"):
        return
    pendente = state.get("pending")
    if not isinstance(pendente, dict):
        return
    try:
        baseline = set(pendente.get("baseline", []))
        caminhos = [
            p for p in icon_files()
            if str(p.resolve()) not in baseline
        ]
        quantidade = int(pendente.get("quantity", 0))
        if quantidade < 1 or len(caminhos) < quantidade:
            return

        selecionadas = caminhos[:quantidade]
        nome = str(pendente.get("name", "Ícone")).strip() or "Ícone"
        await publicar_album_icone(nome, selecionadas)
        state["known_files"] = list(set(state.get("known_files", [])) | {str(p.resolve()) for p in selecionadas})
        state["pending"] = None
        save_json(DATA_FILE, settings)
        await notify_admin(
            f"Ícone publicado automaticamente: {nome}\nFotos: {len(selecionadas)}\nCanal: <#{ICONS_CHANNEL_ID}>"
        )
    except Exception:
        log.exception("Erro no monitoramento automático da pasta Pins")

@monitorar_pasta_icones.before_loop
async def antes_de_monitorar_pasta_icones():
    await bot.wait_until_ready()


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
            if categoria.name.strip().casefold() == "comunidade":
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

    quantidade = sum(1 for c in interaction.guild.categories if c.name.strip().casefold() != "comunidade")
    comunidades = [c.name for c in interaction.guild.categories if c.name.strip().casefold() == "comunidade"]
    embed = discord.Embed(
        title="🔒 Permissões das categorias",
        description=(
            f"Vou alterar **{quantidade} categorias**, preservando a categoria "
            f"**Comunidade** ({', '.join(comunidades) if comunidades else 'não encontrada'}).\n\n"
            "Nas demais categorias, @everyone poderá **ver os canais, usar comandos de aplicativos e interagir com botões**, "
            "mas não poderá enviar mensagens nem criar tópicos. As permissões específicas de outros cargos serão mantidas.\n\n"
            "Confirme apenas se deseja aplicar essa mudança ao servidor."
        ),
        color=discord.Color.dark_grey()
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

            embed = discord.Embed(
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
    embed = discord.Embed(
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
    "admin": {"id": 1561513258211352626, "nome": "ADMINISTRAÇÃO", "posicao": 0},
    "welcome": {"id": 1561513260501569576, "nome": "INÍCIO", "posicao": 1},
    "lojas": {"id": 1561513261424447609, "nome": "LOJAS", "posicao": 2},
    "mercado": {"id": 1561513263286452275, "nome": "MERCADO", "posicao": 3},
    "comunidade": {"id": 1561513265006116877, "nome": "COMUNIDADE", "posicao": 4},
    "estatisticas": {"id": 1561513258970521743, "nome": "ESTATÍSTICAS", "posicao": 5},
}

HIERARQUIA_MOVER_CANAIS = [
    (1561513291703128106, 1561513265006116877, 0),  # chat-geral
    (1561513297155592253, 1561513265006116877, 1),  # duvidas
    (1561513293124739173, 1561513265006116877, 2),  # divulgação
    (1561513290473934998, 1561513265006116877, 3),  # icon
    (1561513293934362705, 1561513265006116877, 4),  # packs-gratis
    (1561513295352037507, 1561513265006116877, 5),  # projetos-gratis
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

    @discord.ui.button(label="Aplicar organização", emoji="✅", style=discord.ButtonStyle.success)
    async def aplicar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode confirmar.", ephemeral=True)
            return
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("Use esta confirmação no servidor correto.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        erros = []
        alteracoes = 0
        try:
            me = guild.me
            if me is None or not me.guild_permissions.manage_channels:
                await interaction.edit_original_response(
                    content="❌ O CC BOT precisa da permissão **Gerenciar canais** para reorganizar a hierarquia.",
                    view=None
                )
                return

            atuais = await guild.fetch_channels()
            categorias = {c.id: c for c in atuais if isinstance(c, discord.CategoryChannel)}
            canais = {c.id: c for c in atuais if not isinstance(c, discord.CategoryChannel)}

            for item in sorted(HIERARQUIA_CATEGORIAS.values(), key=lambda x: x["posicao"]):
                categoria = categorias.get(item["id"])
                if categoria is None:
                    erros.append(f"Categoria ausente: {item['nome']} (ID {item['id']})")
                    continue
                if categoria.name != item["nome"] or categoria.position != item["posicao"]:
                    await categoria.edit(
                        name=item["nome"],
                        position=item["posicao"],
                        reason="Organização confirmada pelo painel administrativo do CC BOT"
                    )
                    alteracoes += 1

            for canal_id, categoria_id, posicao in HIERARQUIA_MOVER_CANAIS:
                canal = canais.get(canal_id)
                categoria = categorias.get(categoria_id)
                if canal is None or categoria is None:
                    erros.append(f"Canal/categoria ausente: canal {canal_id}, categoria {categoria_id}")
                    continue
                if canal.category_id != categoria_id:
                    await canal.edit(
                        category=categoria,
                        reason="Organização confirmada pelo painel administrativo do CC BOT"
                    )
                    alteracoes += 1

            # Categorias antigas são mantidas; nenhuma categoria é apagada automaticamente.

            # Reordena canais e categorias globalmente, mantendo as categorias antigas.
            canais_finais = await guild.fetch_channels()
            por_id = {c.id: c for c in canais_finais}
            ordem_categorias = [1561513258211352626, 1561513260501569576, 1561513261424447609, 1561513263286452275, 1561513265006116877, 1561513258970521743]
            filhos_por_categoria = {
                1561513258211352626: [1561513268021821460, 1561698571525423115],
                1561513260501569576: [1561513269754068992, 1561513274737168414, 1561513277207347310, 1561513277882634404, 1561837934649409696],
                1561513261424447609: [1561513281049595958, 1561513282265944104, 1561513283171651644],
                1561513263286452275: [1561513284811624480, 1561513286967500841, 1561513288297353286, 1561513289438199878, 1561513284191125625],
                1561513265006116877: [1561513291703128106, 1561513297155592253, 1561513293124739173, 1561513290473934998, 1561513293934362705, 1561513295352037507],
                1561513258970521743: [1561513270819692606, 1561513273554374686, 1561513275403800709],
            }
            ordem_final = []
            for categoria_id in ordem_categorias:
                if categoria_id in por_id:
                    ordem_final.append(por_id[categoria_id])
                ordem_final.extend(por_id[cid] for cid in filhos_por_categoria[categoria_id] if cid in por_id)
            # Mantém as duas categorias antigas no fim, sem apagá-las.
            for categoria_antiga_id in (1561513262405648404, 1561513266960666654):
                if categoria_antiga_id in por_id:
                    ordem_final.append(por_id[categoria_antiga_id])
            for posicao_global, canal in enumerate(ordem_final):
                if canal.position != posicao_global:
                    await canal.edit(position=posicao_global, reason="Ordem final confirmada pelo administrador")
                    alteracoes += 1

            texto = f"✅ Organização concluída. Alterações realizadas: **{alteracoes}**."
            if erros:
                texto += "\n\n⚠️ Alguns itens precisam de revisão:\n" + "\n".join(f"• {e}" for e in erros[:8])
            texto += "\n\nNão alterei permissões de cargos ou canais. Revise o acesso herdado das novas categorias."
            await interaction.edit_original_response(content=texto, view=None)
            log.info("Organização de hierarquia concluída: guild=%s alterações=%s erros=%s", guild.id, alteracoes, len(erros))
        except discord.Forbidden:
            log.exception("Permissão insuficiente durante a organização da hierarquia")
            await interaction.edit_original_response(
                content="❌ O Discord negou uma alteração. Verifique **Gerenciar canais** e a posição do cargo do bot. Algumas etapas podem já ter sido aplicadas; gere um novo relatório para conferir.",
                view=None
            )
        except Exception as exc:
            log.exception("Falha ao organizar a hierarquia")
            await interaction.edit_original_response(
                content=f"❌ A organização parou em {type(exc).__name__}. Algumas etapas podem já ter sido aplicadas; gere um novo relatório para conferir.",
                view=None
            )

    @discord.ui.button(label="Cancelar", emoji="✖️", style=discord.ButtonStyle.danger)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode cancelar.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content="Organização cancelada. Nenhuma alteração foi feita.",
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

    embed = discord.Embed(
        title="🧭 Confirmar organização da hierarquia",
        description=(
            "**O que será feito:**\n"
            "• Renomear e ordenar as categorias principais.\n"
            "• Mover duvidas, icon, packs-gratis e projetos-gratis para **COMUNIDADE**.\n"
            "• Mover vendas para **MERCADO**.\n"
            "• Ordenar os canais dentro das categorias.\n"
            "• Manter as categorias antigas vazias, sem apagar categorias.\n\n"
            "**Não serão apagados canais nem alteradas permissões de cargos.**\n\n"
            "⚠️ **Atenção:** ao mover canais, permissões herdadas da categoria de destino podem mudar quem consegue ver ou escrever. Depois, confira principalmente chat-geral, duvidas e os canais de publicação.\n\n"
            "Confirme somente se quiser aplicar essas mudanças agora."
        ),
        color=discord.Color.orange()
    )
    await interaction.response.edit_message(
        embed=embed,
        view=ConfirmarOrganizacaoHierarquiaView(interaction.guild.id)
    )


class CategoriaPainelSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Selecione uma área para gerenciar...",
            min_values=1,
            max_values=1,
            custom_id="ccbot:painel_categoria",
            options=[
                discord.SelectOption(label="Gerenciar DJs", description="Adicionar, alterar ou excluir DJs", emoji="🎧", value="djs"),
                discord.SelectOption(label="Boas-vindas", description="Testar a mensagem de entrada", emoji="👋", value="boas_vindas"),
                discord.SelectOption(label="Emojis, backup e avisos", description="Importar emojis, criar backup e definir canal de avisos", emoji="🧰", value="ferramentas"),
                discord.SelectOption(label="Publicar painéis", description="Samples, acapellas, arrumador, beats e pack personalizado", emoji="📋", value="publicar"),
                discord.SelectOption(label="Testes e manutenção", description="Verificar se o bot está respondendo", emoji="🛠️", value="manutencao"),
                discord.SelectOption(label="Limpar mensagens do PV", description="Apagar mensagens enviadas pelo CC BOT na sua DM", emoji="🧹", value="limpar_pv"),
                discord.SelectOption(label="Gerenciar ícones", description="Monitorar Pinterest e publicar álbuns de fotos", emoji="🖼️", value="icones"),
                discord.SelectOption(label="Permissões das categorias", description="Somente leitura, mantendo bots utilizáveis e Comunidade intacta", emoji="🔒", value="permissoes_categorias"),
                discord.SelectOption(label="Trabalhos ON", description="Publicar aviso no canal e marcar todos", emoji="📣", value="trabalhos_on"),
                discord.SelectOption(label="Analisar hierarquia", description="Enviar categorias, canais, cargos e permissões no PV", emoji="🧭", value="analisar_hierarquia"),
                discord.SelectOption(label="Organizar hierarquia", description="Reordenar categorias e canais com confirmação", emoji="🗂️", value="organizar_hierarquia"),
            ]
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode usar este painel.", ephemeral=True)
            return

        categoria = self.values[0]
        if categoria == "djs":
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="🎧 Gerenciamento de DJs",
                    description="Configure quais DJs aparecem no painel **Gerar Samples**.\n\nUse os botões abaixo para adicionar, alterar ou excluir nomes.",
                    color=discord.Color.green()
                ),
                view=PainelDJsView()
            )
        elif categoria == "boas_vindas":
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="👋 Boas-vindas",
                    description=f"Canal configurado: <#{WELCOME_CHANNEL_ID}>\n\nUse o botão para publicar uma mensagem de teste nesse canal.",
                    color=discord.Color.green()
                ),
                view=PainelBoasVindasView()
            )
        elif categoria == "publicar":
            await interaction.response.edit_message(
                embed=discord.Embed(
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
        elif categoria == "icones":
            await interaction.response.edit_message(
                embed=criar_embed_painel_icones(),
                view=PainelIconesView()
            )
        elif categoria == "limpar_pv":
            await interaction.response.edit_message(
                embed=discord.Embed(
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
                embed=discord.Embed(
                    title="🧰 Ferramentas administrativas",
                    description=(
                        "**Adicionar emojis:** importa as imagens da pasta EMOJIS no Termux.\n\n"
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
                embed=discord.Embed(
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
        self.add_item(PublicarPainelButton("Adicionar emojis", "😀", "emojis"))
        self.add_item(PublicarPainelButton("Arrumador", "🧰", "arrumador"))
        self.add_item(PublicarPainelButton("Acapellas", "🎙️", "acapellas"))
        self.add_item(PublicarPainelButton("Samples", "🎧", "samples"))
        self.add_item(PublicarPainelButton("Venda de beats", "💿", "beats"))
        self.add_item(PublicarPainelButton("Pack personalizado", "📦", "pack"))
        self.add_item(VoltarPainelButton())


class PainelAdminView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(CategoriaPainelSelect())


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
    embed = discord.Embed(
        title="▰  CC BOT  •  CENTRAL ADMINISTRATIVA",
        description=(
            "> **Seu servidor. Suas ferramentas. Um só lugar.**\n"
            "Selecione uma categoria no menu abaixo para abrir os controles.\n"
            "As funções existentes foram mantidas e organizadas por área."
        ),
        color=discord.Color.from_rgb(22, 24, 29),
        timestamp=discord.utils.utcnow()
    )
    embed.set_author(
        name="CC BOT  /  PAINEL DE CONTROLE",
        icon_url=bot.user.display_avatar.url if bot.user else discord.Embed.Empty
    )
    embed.add_field(
        name="🎧  DJs & SAMPLES",
        value="Gerencie os DJs disponíveis no gerador de samples.",
        inline=True
    )
    embed.add_field(
        name="👋  BOAS-VINDAS",
        value="Confira e teste a mensagem de entrada do servidor.",
        inline=True
    )
    embed.add_field(
        name="🧰  FERRAMENTAS",
        value="Adicione emojis, crie backups e escolha o canal de avisos.",
        inline=True
    )
    embed.add_field(
        name="📦  PUBLICAÇÃO",
        value="Samples · Acapellas · Arrumador · Venda de beats · Pack personalizado",
        inline=True
    )
    embed.add_field(
        name="🛠️  SISTEMA",
        value="Verifique a resposta do bot e consulte a latência.",
        inline=True
    )
    embed.add_field(
        name="⚙️  ACESSO RÁPIDO",
        value="Use o seletor abaixo. As opções administrativas são restritas ao proprietário configurado.",
        inline=True
    )
    embed.set_thumbnail(
        url="https://cdn.discordapp.com/ephemeral-attachments/1442529633919570057/1561721929583566858/605081.jpg?ex=6ad76263&is=6ad610e3&hm=23259b8d57737e9607039f616c91ceea22a3e99d47e000691c14e7115ac69edd&"
    )
    embed.set_footer(text="CC BOT  •  ADMIN CONSOLE  •  Todas as funções em um só painel")
    return embed

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
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(
                        media="https://cdn.discordapp.com/ephemeral-attachments/1442529633919570057/1561721929583566858/605081.jpg?ex=6ad76263&is=6ad610e3&hm=23259b8d57737e9607039f616c91ceea22a3e99d47e000691c14e7115ac69edd&"
                    )
                ),
                discord.ui.Separator(
                    visible=True,
                    spacing=discord.SeparatorSpacing.large
                ),
                discord.ui.Section(
                    discord.ui.TextDisplay(
                        content=(
                            "### <:box:1561761831587160140>  GERADOR DE SAMPLES\n"
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


@bot.tree.command(name="painel", description="Abrir o painel de configuração do CC BOT")
async def painel(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("Apenas o administrador pode abrir esse painel.", ephemeral=True)
        return

    await interaction.response.send_message(
        embed=criar_embed_painel(),
        view=PainelAdminView(),
        ephemeral=True
    )




EMOJIS_DIR = Path("/sdcard/Download/EMOJIS")

async def adicionar_emojis(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("Apenas o administrador pode usar este comando.", ephemeral=True)
        return
    if interaction.guild is None:
        await interaction.response.send_message("Use este comando dentro do servidor.", ephemeral=True)
        return

    membro = interaction.guild.me
    if membro is None or not membro.guild_permissions.manage_emojis_and_stickers:
        await interaction.response.send_message("Preciso da permissão Gerenciar Expressões.", ephemeral=True)
        return
    if not EMOJIS_DIR.is_dir():
        await interaction.response.send_message(f"Pasta não encontrada: {EMOJIS_DIR}", ephemeral=True)
        return

    arquivos = sorted(
        x for x in EMOJIS_DIR.iterdir()
        if x.is_file() and x.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    )
    if not arquivos:
        await interaction.response.send_message("Não encontrei imagens na pasta EMOJIS.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    existentes = {e.name.casefold() for e in interaction.guild.emojis}
    ok, ignorados, falhas = [], [], []

    for arquivo in arquivos:
        nome = arquivo.stem.lower().replace("-", "_").replace(" ", "_")
        nome = "".join(ch for ch in nome if ch.isalnum() or ch == "_").strip("_")
        nome = (nome if len(nome) >= 2 else "em_" + nome)[:32]

        if nome.casefold() in existentes:
            ignorados.append(f"{arquivo.name}: já existe")
            continue

        try:
            dados = await asyncio.to_thread(arquivo.read_bytes)
            emoji = await interaction.guild.create_custom_emoji(
                name=nome,
                image=dados,
                reason=f"Importação em lote pelo administrador {interaction.user.id}"
            )
            ok.append(f"{emoji} :{nome}:")
            existentes.add(nome.casefold())
        except Exception as exc:
            falhas.append(f"{arquivo.name}: {type(exc).__name__}")
            if isinstance(exc, discord.Forbidden):
                break

    texto = [
        "## Importação de emojis concluída",
        f"**Adicionados:** {len(ok)}",
        f"**Já existentes:** {len(ignorados)}",
        f"**Falhas:** {len(falhas)}",
    ]
    if ok:
        texto.append("**Adicionados:**\n" + "\n".join(ok[:15]))
    if ignorados:
        texto.append("**Ignorados:**\n" + "\n".join(ignorados[:8]))
    if falhas:
        texto.append("**Falhas:**\n" + "\n".join(falhas[:8]))
    await interaction.followup.send("\n".join(texto)[:1950], ephemeral=True)

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

        embed = discord.Embed(
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
            await interaction.user.send(
                "**🧰 Arrumador de Projetos**\n"
                "Recebi sua descrição. Envie o ZIP aqui no PV se o Discord permitir.\n\n"
                "**Se o arquivo for grande e o Discord pedir Nitro:**\n"
                "1. Envie o ZIP para o Google Drive.\n"
                "2. No Drive, abra **Compartilhar** e mude o acesso para **Qualquer pessoa com o link** (leitor).\n"
                "3. Cole o link aqui nesta conversa privada.\n\n"
                "Aceito anexos ZIP ou links do Google Drive, Dropbox, OneDrive e WeTransfer. Você tem **10 minutos**."
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Não consegui abrir seu PV. Ative mensagens diretas deste servidor e execute /arrumador novamente.",
                ephemeral=True
            )
            return

        await interaction.followup.send(
            "Te enviei as instruções no PV. Se o ZIP for grande demais para o Discord, pode mandar um link de compartilhamento.",
            ephemeral=True
        )

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


class ArrumadorPainelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MandarArrumadorButton())


def criar_embed_arrumador():
    embed = discord.Embed(
        title="🧰 ARRUMADOR DE ARQUIVOS",
        description=(
            "Organize seu projeto do FL Studio Mobile do jeito que você precisa.\n\n"
            "Descreva as mudanças desejadas e envie seu projeto em **.ZIP** pelo PV do bot. "
            "Seu pedido e o arquivo serão encaminhados ao responsável para análise.\n\n"
            "📦 **Como começar**\n"
            "Clique no botão **MANDAR** abaixo e descreva o que deseja mudar."
        ),
        color=discord.Color.red()
    )
    embed.set_thumbnail(
        url="https://cdn.discordapp.com/ephemeral-attachments/1442529633919570057/1561722097011527761/801_Sem_Titulo_20261018095128.png?ex=6ad7628b&is=6ad6110b&hm=5221dbb0652028ae88f6d57d7daa303cb8c86a67373c387f2711e7888011ef69&"
    )
    embed.set_footer(text="CC BOT • Arrumador de Projetos")
    return embed


async def arrumador(interaction: discord.Interaction):
    if await deny_unauthorized(interaction):
        return
    await interaction.response.send_message(
        embed=criar_embed_arrumador(),
        view=ArrumadorPainelView()
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
    embed = discord.Embed(
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



# Painéis de Parcerias e Trampar Conosco — publicados/atualizados sem duplicar mensagens.
PARTNERSHIPS_CHANNEL_ID = 1561843704518549604
WORK_WITH_US_CHANNEL_ID = 1561843767860789318
WHATSAPP_NUMBER = "5511914038635"
CONTACT_DISCORD_ID = ADMIN_ID


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
        embed = discord.Embed(
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
            label="Quero trampar conosco",
            emoji="💼",
            style=discord.ButtonStyle.primary,
            custom_id="ccbot:trabalhar:abrir"
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Qual área você domina ou gostaria de oferecer?",
            view=SelecionarAreaTrabalhoView(),
            ephemeral=True
        )


class TramparConoscoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(AbrirAreasTrabalhoButton())


class ParceriasView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Quero fechar uma parceria",
            style=discord.ButtonStyle.link,
            url=link_whatsapp(
                "Olá! Quero fechar uma parceria com o servidor C.C ©. "
                "Já tenho os requisitos mínimos e gostaria de conversar."
            ),
            emoji="🤝"
        ))


def criar_embed_parcerias() -> discord.Embed:
    embed = discord.Embed(
        title="🤝 Quer fechar uma parceria com o nosso servidor?",
        description=(
            "Quer divulgar seu grupo, projeto ou negócio junto com o C.C ©? "
            "Confira os requisitos antes de entrar em contato.\n\n"
            "**📋 REQUISITOS MÍNIMOS**\n"
            "• Ter um grupo de WhatsApp com, no mínimo, **30 membros**.\n"
            "• Adicionar o responsável pelo C.C © como **administrador do grupo**.\n\n"
            "**🔄 COMO FUNCIONA?**\n"
            "Com a permissão de administrador, o responsável poderá enviar as divulgações "
            "necessárias e organizar a parceria.\n\n"
            "O parceiro também poderá ter uma área específica no servidor C.C © para divulgar "
            "seus produtos, serviços e projetos.\n\n"
            "Os detalhes de cada parceria serão combinados diretamente."
        ),
        color=discord.Color.from_rgb(0, 0, 0)
    )
    embed.set_footer(text="C.C © • Parcerias")
    return embed


def criar_embed_trampar_conosco() -> discord.Embed:
    embed = discord.Embed(
        title="💼 Trampar Conosco — C.C ©",
        description=(
            "Quer contribuir com o crescimento do C.C ©? Estamos planejando oportunidades "
            "para pessoas com diferentes habilidades. **Você não precisa saber fazer tudo:** "
            "cada área é independente.\n\n"
            "**🛠️ ÁREAS DE INTERESSE**\n"
            "• **Bots para Discord:** desenvolvimento em Python, com ou sem IA.\n"
            "• **Design:** banners, artes e materiais para o servidor.\n"
            "• **Organização de servidor / Staff:** estrutura e organização do Discord.\n"
            "• **Edição de vídeo:** prevista para uma próxima etapa.\n\n"
            "**🎁 BENEFÍCIO**\n"
            "Quem participar poderá ter uma categoria específica no servidor para divulgar "
            "seus trabalhos, produtos, serviços e projetos. Novas áreas poderão ser adicionadas futuramente.\n\n"
            "**⚠️ INSCRIÇÕES**\n"
            "**No momento, não estamos aceitando novos integrantes.** Esta aba informa "
            "as áreas de interesse para oportunidades futuras.\n\n"
            "Clique no botão abaixo e selecione a área em que você trabalha para ver as opções de contato."
        ),
        color=discord.Color.from_rgb(0, 0, 0)
    )
    embed.set_footer(text="C.C © • Trampar Conosco")
    return embed


async def publicar_paineis_comunidade():
    """Cria os painéis uma vez e edita as mesmas mensagens em inicializações futuras."""
    paineis = [
        ("Parcerias", PARTNERSHIPS_CHANNEL_ID, criar_embed_parcerias, ParceriasView),
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
            mensagem = None
            if mensagem_id:
                try:
                    mensagem = await canal.fetch_message(int(mensagem_id))
                except discord.NotFound:
                    mensagem = None
            if mensagem is None:
                mensagem = await canal.send(embed=criar_embed(), view=criar_view())
                registros[nome] = {"message_id": mensagem.id}
                save_json(DATA_FILE, settings)
                log.info("Painel %s publicado no canal %s (mensagem %s)", nome, canal_id, mensagem.id)
            else:
                await mensagem.edit(embed=criar_embed(), view=criar_view())
                log.info("Painel %s existente atualizado no canal %s (mensagem %s)", nome, canal_id, mensagem.id)
        except Exception:
            log.exception("Falha ao publicar/atualizar o painel %s", nome)

SALES_SOURCE_CHANNEL_ID = 1561513284191125625
SALES_COUNTER_CHANNEL_ID = 1561513273554374686
SALES_COUNTER_PREFIX = "│💰﹒Vendas : "


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
    log.info("Contagem recalculada: %s mensagens atuais no canal de vendas", total)


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    if payload.channel_id != SALES_SOURCE_CHANNEL_ID:
        return
    try:
        await contar_mensagens_do_canal()
    except Exception:
        log.exception("Falha ao recalcular vendas após exclusão de mensagem")


@bot.event
async def on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent):
    if payload.channel_id != SALES_SOURCE_CHANNEL_ID:
        return
    try:
        await contar_mensagens_do_canal()
    except Exception:
        log.exception("Falha ao recalcular vendas após exclusão em massa")


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

        embed = discord.Embed(
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

        embed = discord.Embed(
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
            bot.tree.clear_commands(guild=None)
            # Não sincronize a árvore global vazia: o Discord protege o comando
            # Entry Point das Activities contra exclusão em atualizações em lote.
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

        try:
            servidor = bot.get_guild(1561513043886743562)
            roles = await garantir_cargos(servidor)
            fundador = roles.get("fundador")
            administrador = servidor.get_member(ADMIN_ID) if servidor else None
            if fundador and administrador and fundador not in administrador.roles:
                await administrador.add_roles(fundador, reason="Cargo Fundador da comunidade")
        except Exception:
            log.exception("Falha ao configurar cargos na inicialização.")

        # Repara mensagens existentes antes das tarefas secundárias de inicialização.
        if not getattr(bot, "_panels_restored_this_process", False):
            await restaurar_paineis_existentes()
            bot._panels_restored_this_process = True

        # Corrige publicações antigas de beats cujos botões não tinham IDs persistentes.
        if not getattr(bot, "_beats_migrated_this_process", False):
            await restaurar_botoes_beats_existentes()
            bot._beats_migrated_this_process = True

        # Ponte GitHub: recebe tarefas aprovadas no repositório e publica relatórios.
        tarefa_ponte = getattr(bot, "_github_bridge_task", None)
        if tarefa_ponte is None or tarefa_ponte.done():
            bot._github_bridge_task = asyncio.create_task(github_bridge.run(bot))

        if not getattr(bot, "_sales_history_counted", False):
            await contar_mensagens_do_canal()
            bot._sales_history_counted = True

        if not atualizar_contador_vendas.is_running():
            atualizar_contador_vendas.start()


        channel_id = settings.get("notice_channel", DEFAULT_NOTICE_CHANNEL)
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))

        embed = discord.Embed(
            title="CC BOT iniciado com sucesso!",
            description=(
                "🟢 **Status:** Online\n"
                f"🤖 **Bot:** {bot.user.mention}\n"
                f"⚙️ **Comandos sincronizados:** {len(synced)}"
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_image(url=random.choice(STARTUP_GIFS))
        embed.set_footer(text="CC BOT • Sistema de inicialização")

        # Mantém apenas a mensagem de inicialização mais recente do próprio bot.
        async for mensagem in channel.history(limit=100):
            if mensagem.author.id != bot.user.id or not mensagem.embeds:
                continue
            if mensagem.embeds[0].title == "CC BOT iniciado com sucesso!":
                try:
                    await mensagem.delete()
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    log.warning("Sem permissão para apagar a mensagem anterior de inicialização.")
                break

        await channel.send(embed=embed)

        # Os componentes persistentes já foram registrados no início do on_ready.
        # O painel de Parcerias usa somente botão de link; não precisa de callback.

        # Publica/atualiza os painéis de Parcerias e Trampar Conosco sem duplicar mensagens.
        await publicar_paineis_comunidade()

        # Republica os três embeds promocionais uma única vez após esta atualização.
        # A marca é salva em config.json para impedir duplicações em reinícios futuros.
        await publicar_embeds_promocionais()

        # Inicia o monitor de fotos baixadas do Pinterest.
        if not monitorar_pasta_icones.is_running():
            monitorar_pasta_icones.start()

        # Atualiza a mensagem do painel já publicada, sem criar outra.
        try:
            samples_channel = await bot.fetch_channel(SAMPLES_PANEL_CHANNEL_ID)
            samples_message = await samples_channel.fetch_message(SAMPLES_PANEL_MESSAGE_ID)
            await samples_message.edit(view=GeradorSamplesView())
            log.info("Painel /samples existente atualizado com o botão Gerar Samples.")
        except Exception:
            log.exception("Não foi possível atualizar o painel /samples existente.")

        # Atualiza o painel de Pack Personalizado já publicado, sem criar outro.
        try:
            pack_channel = await bot.fetch_channel(PACK_PANEL_CHANNEL_ID)
            pack_message = await pack_channel.fetch_message(PACK_PANEL_MESSAGE_ID)
            await pack_message.edit(view=PackPersonalizadoView())
            log.info("Painel Pack Personalizado existente atualizado: canal=%s mensagem=%s", PACK_PANEL_CHANNEL_ID, PACK_PANEL_MESSAGE_ID)
        except Exception:
            log.exception("Não foi possível atualizar o painel Pack Personalizado existente.")

    except Exception as exc:
        log.exception("Falha ao iniciar ou sincronizar comandos")
        await notify_admin(f"Falha ao iniciar o CC BOT: {exc}")

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
