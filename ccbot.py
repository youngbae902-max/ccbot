import os
import asyncio
import json
import logging
import zipfile
import io
import uuid
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

import discord
from discord.ext import commands, tasks
from discord import app_commands

BASE = Path.home() / "ccbot-repo"
CONFIG = BASE / "config"
DATA_FILE = CONFIG / "config.json"
MODELS_FILE = CONFIG / "modelos.json"
DJS_FILE = CONFIG / "djs.json"
TOKEN_FILE = Path("/sdcard/Download/CC/token.txt")
BACKUP_DIR = Path("/sdcard/Download/CC/Backups")
BEAT_PREVIEWS_DIR = BASE / "data" / "beat_previews"

ADMIN_ID = 1366109646934380614
DEFAULT_NOTICE_CHANNEL = 1561698571525423115
WELCOME_CHANNEL_ID = 1561513269754068992

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

# A lista só contém DJs cadastrados pelo administrador no comando /painel.
mcs = load_json(DJS_FILE, [])
if not isinstance(mcs, list):
    mcs = []
    save_json(DJS_FILE, mcs)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
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


async def enviar_pedido_admin(mensagem, embed=None):
    """Envia cada pedido tanto ao PV do administrador quanto ao canal de avisos."""
    erros = []
    try:
        administrador = bot.get_user(ADMIN_ID) or await bot.fetch_user(ADMIN_ID)
        await administrador.send(content=mensagem[:1900] if mensagem else None, embed=embed)
    except Exception as exc:
        erros.append(f"PV: {type(exc).__name__}")
        log.exception("Não foi possível enviar pedido por PV ao administrador.")

    try:
        canal_id = int(settings.get("notice_channel", DEFAULT_NOTICE_CHANNEL))
        canal = bot.get_channel(canal_id)
        if canal is None:
            canal = await bot.fetch_channel(canal_id)
        await canal.send(content=mensagem[:1900] if mensagem else None, embed=embed)
    except Exception as exc:
        erros.append(f"canal de avisos: {type(exc).__name__}")
        log.exception("Não foi possível enviar pedido ao canal de avisos.")

    if len(erros) == 2:
        raise RuntimeError("Falha no envio do pedido para PV e canal de avisos: " + "; ".join(erros))
    if erros:
        log.warning("Pedido enviado parcialmente; falha em %s", ", ".join(erros))



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
            await canal.send(embed=embed, view=view)
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
        super().__init__(label="Ouvir prévia", emoji="🎧", style=discord.ButtonStyle.primary)
        self.preview_path = Path(preview_path)

    async def callback(self, interaction: discord.Interaction):
        if not self.preview_path.is_file():
            await interaction.response.send_message(
                "A prévia não está disponível no armazenamento do bot.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            content="Prévia do beat",
            file=discord.File(self.preview_path),
            ephemeral=True
        )


class ComprarBeatButton(discord.ui.Button):
    def __init__(self, nome: str, preco: str):
        super().__init__(label="Comprar beat", emoji="🛒", style=discord.ButtonStyle.success)
        self.nome = nome
        self.preco = preco

    async def callback(self, interaction: discord.Interaction):
        try:
            await enviar_pedido_admin(
                f"🛒 **Pedido de compra de beat**\nBeat: {self.nome}\nValor: {self.preco}\n"
                f"Comprador: {interaction.user} (ID {interaction.user.id})\n"
                f"Servidor: {interaction.guild.name if interaction.guild else 'DM'}"
            )
            await interaction.response.send_message(
                f"🛒 Seu interesse em **{self.nome}** ({self.preco}) foi enviado ao administrador. Aguarde o contato para concluir a compra.",
                ephemeral=True
            )
        except Exception:
            log.exception("Falha ao registrar interesse de compra de beat")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Não consegui registrar seu pedido agora. Tente novamente mais tarde.",
                    ephemeral=True
                )


class VendaBeatView(discord.ui.View):
    def __init__(self, nome: str, preco: str, preview_path):
        super().__init__(timeout=None)
        self.add_item(OuvirPreviaBeatButton(preview_path))
        self.add_item(ComprarBeatButton(nome, preco))


@bot.tree.command(name="venda-de-beats", description="Cadastrar e publicar um beat para venda")
async def venda_de_beats(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Apenas o administrador pode cadastrar beats para venda.",
            ephemeral=True
        )
        return
    await interaction.response.send_modal(BeatInfoModal())



class PackBasicoModal(discord.ui.Modal, title="Pack Básico"):
    nome = discord.ui.TextInput(label="Nome do pack", max_length=100)
    estilo = discord.ui.TextInput(label="Estilo musical", placeholder="Ex.: funk mandelão", max_length=100)
    detalhes = discord.ui.TextInput(label="Detalhes do pedido", style=discord.TextStyle.paragraph, max_length=900)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_message(
                view=PackAdicionaisView(interaction.user.id, str(self.nome), str(self.estilo), str(self.detalhes)),
                ephemeral=True
            )
        except Exception as exc:
            log.exception("Falha ao abrir adicionais do Pack Básico")
            if not interaction.response.is_done():
                await interaction.response.send_message("Não consegui abrir os adicionais. Tente novamente.", ephemeral=True)


class PackPremiumModal(discord.ui.Modal, title="Pack Premium"):
    nome = discord.ui.TextInput(label="Nome do pack", max_length=100)
    estilo = discord.ui.TextInput(label="Estilo musical", max_length=100)
    itens = discord.ui.TextInput(label="Itens desejados", placeholder="Kicks, samples, acapellas, MCs, vozes antes da virada", style=discord.TextStyle.paragraph, max_length=900)
    quantidades = discord.ui.TextInput(label="Quantidades e organização", style=discord.TextStyle.paragraph, max_length=500, required=False)

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
                discord.SelectOption(label="Organização das pastas (+ R$ 10)", value="Organização das pastas (+ R$ 10)"),
                discord.SelectOption(label="Samples personalizadas (+ R$ 10)", value="Samples personalizadas (+ R$ 10)"),
                discord.SelectOption(label="Samples de outros packs (+ R$ 20)", value="Samples de outros packs (+ R$ 20)")
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
                "## <:tag:1561787923865665667> PACK BÁSICO — R$ 50,00\n"
                "Inclui cerca de 3 horas de trabalho (R$ 30) + conhecimento aplicado (R$ 20).\n\n"
                "**Adicionais opcionais:** organização +R$ 10; samples personalizadas +R$ 10; "
                "samples de outros packs +R$ 20. Selecione os que deseja."
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
            interaction, "Pack Básico", valor,
            {"Nome do pack": self.nome, "Estilo musical": self.estilo, "Detalhes": self.detalhes},
            extras
        )
        try:
            await interaction.edit_original_response(view=None)
        except discord.HTTPException:
            pass


class PackPersonalizadoView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        basico = discord.ui.Button(label="PACK BÁSICO", emoji="<:tag:1561787923865665667>", style=discord.ButtonStyle.primary, custom_id="ccbot:pack:basico")
        premium = discord.ui.Button(label="PACK PREMIUM", emoji="<:coroa:1561788313214390364>", style=discord.ButtonStyle.success, custom_id="ccbot:pack:premium")
        basico.callback = self.abrir_basico
        premium.callback = self.abrir_premium
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(content=(
                "## <:box:1561761831587160140> PACK PERSONALIZADO\n"
                "Monte um pack exclusivo do seu jeito.\n\n"
                "### <:tag:1561787923865665667> PACK BÁSICO — R$ 50,00\n"
                "Cerca de 3 horas de trabalho + conhecimento aplicado. Adicionais opcionais.\n\n"
                "### <:coroa:1561788313214390364> PACK PREMIUM — R$ 100,00\n"
                "Personalização completa com kicks, samples, acapellas, MCs, vozes antes da virada e pastas organizadas."
            )),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
            discord.ui.ActionRow(basico, premium)
        ))

    async def abrir_basico(self, interaction):
        try:
            await interaction.response.send_modal(PackBasicoModal())
        except Exception:
            log.exception("Falha no botão Pack Básico")
            if not interaction.response.is_done():
                await interaction.response.send_message("Não consegui abrir o formulário básico. Execute /pack-personalizado novamente.", ephemeral=True)

    async def abrir_premium(self, interaction):
        try:
            await interaction.response.send_modal(PackPremiumModal())
        except Exception:
            log.exception("Falha no botão Pack Premium")
            if not interaction.response.is_done():
                await interaction.response.send_message("Não consegui abrir o formulário premium. Execute /pack-personalizado novamente.", ephemeral=True)


@bot.tree.command(name="criar-pack-personalizado", description="Publicar o painel público de Pack Personalizado")
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
        await interaction.response.edit_message(
            view=discord.ui.LayoutView(
                discord.ui.Container(
                    discord.ui.TextDisplay(
                        content=(
                            "## 🎧 DJ SELECIONADO\n"
                            f"**{discord.utils.escape_markdown(nome)}**\n\n"
                            "A seleção foi recebida. A geração automática dos arquivos de áudio ainda precisa ser integrada."
                        )
                    )
                )
            )
        )


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
                discord.SelectOption(label="Testes e manutenção", description="Verificar se o bot está respondendo", emoji="🛠️", value="manutencao"),
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
        title="<:box:1561761831587160140> Central administrativa do **CC BOT**",
        description=(
            "Selecione uma área para abrir suas opções.\n\n"
            "### 🎧 Gerenciamento de DJs\n"
            "Adicione, altere ou remova os DJs do gerador.\n\n"
            "### 👋 Boas-vindas\n"
            "Teste a mensagem de entrada do servidor.\n\n"
            "### 🧰 Emojis, backup e avisos\n"
            "Importe emojis, crie backups e configure o canal de avisos.\n\n"
            "### 🛠️ Testes e manutenção\n"
            "Confira se o bot está online e respondendo.\n\n"
            "-# A central será ampliada conforme novas ferramentas administrativas forem criadas."
        ),
        color=discord.Color.green()
    )
    embed.set_footer(text="CC BOT • Central administrativa")
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

@bot.tree.command(name="adicionar_emojis", description="Adicionar emojis da pasta EMOJIS ao servidor")
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

@bot.tree.command(name="samples", description="Publicar o painel do Gerador de Samples")
async def samples(interaction: discord.Interaction):
    if await deny_unauthorized(interaction):
        return
    await interaction.response.send_message(view=GeradorSamplesView())

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
                "**🧰 Arrumador de Projetos**\\n"
                "Recebi sua descrição. Envie o ZIP aqui no PV se o Discord permitir.\\n\\n"
                "**Se o arquivo for grande e o Discord pedir Nitro:**\\n"
                "1. Envie o ZIP para o Google Drive.\\n"
                "2. No Drive, abra **Compartilhar** e mude o acesso para **Qualquer pessoa com o link** (leitor).\\n"
                "3. Cole o link aqui nesta conversa privada.\\n\\n"
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
                        "🧰 **Novo pedido — Arrumador de Projetos**\\n"
                        f"**Pessoa:** {interaction.user} (`{interaction.user.id}`)\\n"
                        f"**Pedido de mudança:**\\n{self.mudancas.value}\\n"
                        "**Arquivo:** enviado por link externo (não anexado ao Discord)\\n"
                        f"**Link do arquivo:** {url_valida}\\n"
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
                            + (f"\\n⚠️ O envio falhou em: {', '.join(erros)}." if erros else "")
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
                "🧰 **Novo pedido — Arrumador de Projetos**\\n"
                f"**Pessoa:** {interaction.user} (`{interaction.user.id}`)\\n"
                f"**Pedido de mudança:**\\n{self.mudancas.value}\\n"
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
                + (f"\\n⚠️ O envio falhou em: {', '.join(erros)}." if erros else "")
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


class ArrumadorPainelView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Container(
                discord.ui.Section(
                    discord.ui.TextDisplay(
                        content=(
                            "## 🧰 ARRUMADOR DE ARQUIVOS\n"
                            "Organize seu projeto do FL Studio Mobile do jeito que você precisa.\n\n"
                            "Descreva as mudanças desejadas e, em seguida, envie seu projeto em **.ZIP** pelo PV do bot. "
                            "Seu pedido e o arquivo serão encaminhados ao responsável para análise.\n\n"
                            "-# Clique em **MANDAR** para começar."
                        )
                    ),
                    accessory=discord.ui.Thumbnail(
                        media="https://cdn.discordapp.com/ephemeral-attachments/1442529633919570057/1561722097011527761/801_Sem_Titulo_20261018095128.png?ex=6ad7628b&is=6ad6110b&hm=5221dbb0652028ae88f6d57d7daa303cb8c86a67373c387f2711e7888011ef69&"
                    )
                ),
                discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
                discord.ui.ActionRow(MandarArrumadorButton())
            )
        )


@bot.tree.command(name="arrumador", description="Publicar o painel do Arrumador de Arquivos")
async def arrumador(interaction: discord.Interaction):
    if await deny_unauthorized(interaction):
        return
    await interaction.response.send_message(view=ArrumadorPainelView())




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


@bot.event
async def on_member_join(member: discord.Member):
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
async def on_ready():
    try:
        guild = discord.Object(id=1561513043886743562)

        # Remove registros globais antigos para não exibir a mesma slash command
        # junto da versão registrada no servidor.
        global_commands = await bot.tree.fetch_commands()
        for command in global_commands:
            await bot.tree.delete_command(command.id)
            log.info("Comando global antigo removido: /%s (id=%s)", command.name, command.id)

        # Registra uma única cópia de cada comando no servidor de destino.
        bot.tree.clear_commands(guild=guild)
        bot.tree.copy_global_to(guild=guild)
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        synced = await bot.tree.sync(guild=guild)
        log.info("Comandos sincronizados somente no servidor %s: %s", guild.id, len(synced))
        log.info("Conectado como %s", bot.user)

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

        # Atualiza a mensagem do painel já publicada, sem criar outra.
        try:
            samples_channel = await bot.fetch_channel(SAMPLES_PANEL_CHANNEL_ID)
            samples_message = await samples_channel.fetch_message(SAMPLES_PANEL_MESSAGE_ID)
            await samples_message.edit(view=GeradorSamplesView())
            bot.add_view(GeradorSamplesView())
            log.info("Painel /samples existente atualizado com o botão Gerar Samples.")
        except Exception:
            log.exception("Não foi possível atualizar o painel /samples existente.")

        bot.add_view(ArrumadorPainelView())
        log.info("View persistente do painel Arrumador registrada.")

        # Atualiza o painel de Pack Personalizado já publicado, sem criar outro.
        try:
            pack_channel = await bot.fetch_channel(PACK_PANEL_CHANNEL_ID)
            pack_message = await pack_channel.fetch_message(PACK_PANEL_MESSAGE_ID)
            await pack_message.edit(view=PackPersonalizadoView())
            bot.add_view(PackPersonalizadoView(), message_id=PACK_PANEL_MESSAGE_ID)
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

if not TOKEN_FILE.is_file():
    raise SystemExit(
        f"Token não encontrado em {TOKEN_FILE}. Crie o arquivo com o token do bot."
    )

TOKEN = TOKEN_FILE.read_text(encoding="utf-8").strip()
if not TOKEN:
    raise SystemExit("O arquivo token.txt está vazio.")

bot.run(TOKEN)
