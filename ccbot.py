import os
import asyncio
import json
import logging
import zipfile
from pathlib import Path
from datetime import datetime

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

ADMIN_ID = 1366109646934380614
DEFAULT_NOTICE_CHANNEL = 1561698571525423115
WELCOME_CHANNEL_ID = 1561513269754068992

# REGRA PERMANENTE DE MANUTENÇÃO:
# Ao alterar painéis do Discord, edite a mensagem já publicada sempre que possível.
# Não publique outro embed/painel nem duplique mensagens sem pedido explícito do administrador
# ou necessidade técnica real. O painel de Samples existente está identificado abaixo.
SAMPLES_PANEL_CHANNEL_ID = 1561513284811624480
SAMPLES_PANEL_MESSAGE_ID = 1561742192656261183

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
        await canal.send(
            f"{interaction.user.mention} **Mande um áudio para ser a prévia do BEAT.**\n"
            "Aceito arquivos de áudio. Você tem 3 minutos para enviar a prévia neste canal."
        )

        def mensagem_valida(message: discord.Message):
            return (
                message.author.id == interaction.user.id
                and message.channel.id == interaction.channel_id
                and bool(message.attachments)
            )

        try:
            mensagem = await bot.wait_for("message", check=mensagem_valida, timeout=180)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "Tempo esgotado. Execute /venda-de-beats novamente para tentar cadastrar o beat.",
                ephemeral=True
            )
            return

        audio = None
        for arquivo in mensagem.attachments:
            nome_arquivo = arquivo.filename.lower()
            tipo = (arquivo.content_type or "").lower()
            if tipo.startswith("audio/") or Path(nome_arquivo).suffix in BEAT_AUDIO_EXTENSIONS:
                audio = arquivo
                break

        if audio is None:
            await interaction.followup.send(
                "Esse arquivo não parece ser um áudio. Envie um arquivo de áudio e tente novamente com /venda-de-beats.",
                ephemeral=True
            )
            try:
                await mensagem.delete()
            except discord.HTTPException:
                pass
            return

        preco = f"R$ {valor_numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        embed = discord.Embed(
            title=f"🎧 {self.nome.value.strip()}",
            description=self.descricao.value.strip(),
            color=discord.Color.green()
        )
        embed.add_field(name="Estilo", value=self.estilo.value.strip(), inline=True)
        embed.add_field(name="Valor", value=preco, inline=True)
        embed.set_footer(text="CC BOT • Venda de Beats")

        view = VendaBeatView(
            nome=self.nome.value.strip(),
            preco=preco,
            preview_url=audio.url
        )
        await canal.send(embed=embed, view=view)
        await interaction.followup.send("Beat publicado com sucesso neste canal.", ephemeral=True)


class OuvirPreviaBeatButton(discord.ui.Button):
    def __init__(self, preview_url: str):
        super().__init__(label="Ouvir prévia", emoji="🎧", style=discord.ButtonStyle.primary)
        self.preview_url = preview_url

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"🎧 **Prévia do beat:** {self.preview_url}",
            ephemeral=True
        )


class ComprarBeatButton(discord.ui.Button):
    def __init__(self, nome: str, preco: str):
        super().__init__(label="Comprar beat", emoji="🛒", style=discord.ButtonStyle.success)
        self.nome = nome
        self.preco = preco

    async def callback(self, interaction: discord.Interaction):
        try:
            await notify_admin(
                f"Pedido de compra de beat\nBeat: {self.nome}\nValor: {self.preco}\n"
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
    def __init__(self, nome: str, preco: str, preview_url: str):
        super().__init__(timeout=None)
        self.add_item(OuvirPreviaBeatButton(preview_url))
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

@bot.tree.command(name="arrumador", description="Estado do módulo Arrumador de Projetos")
async def arrumador(interaction: discord.Interaction):
    if await deny_unauthorized(interaction):
        return
    await interaction.response.send_message(
        "O módulo está reservado na estrutura. Falta integrar a lógica real do arrumador.",
        ephemeral=True
    )



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
