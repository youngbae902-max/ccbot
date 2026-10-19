import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

# =====================================
# CC BOT — CONFIGURAÇÃO PRINCIPAL
# =====================================

TOKEN_FILE = Path("/sdcard/Download/CC/token.txt")
AUTHORIZED_ID = 1366109646934380614
NOTICE_CHANNEL_ID = 1561698571525423115

DATA_DIR = Path.home() / "ccbot_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

PRODUCTS_FILE = DATA_DIR / "products.json"
CONFIG_FILE = DATA_DIR / "config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("CCBOT")


def read_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, data):
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp.replace(path)


products = read_json(PRODUCTS_FILE, [])
config = read_json(CONFIG_FILE, {
    "sales_channel": None,
    "welcome_channel": None,
    "welcome_message": "Bem-vindo(a), {user}!",
})


async def authorized(interaction: discord.Interaction) -> bool:
    # Usuários não autorizados são ignorados silenciosamente.
    return interaction.user.id == AUTHORIZED_ID


async def send_notice(message):
    log.warning(message)
    try:
        channel = bot.get_channel(NOTICE_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(NOTICE_CHANNEL_ID)
        await channel.send(
            f"⚠️ **CC BOT — Aviso em tempo real**\n{message}"
        )
    except Exception:
        log.exception("Não foi possível enviar o aviso ao Discord.")


# =====================================
# BOT
# =====================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

class CCBot(commands.Bot):
    async def setup_hook(self):
        try:
            synced = await self.tree.sync()
            log.info("%s comandos slash sincronizados.", len(synced))
        except Exception as exc:
            log.exception("Falha ao sincronizar comandos.")
            await send_notice(f"Falha ao sincronizar comandos: {exc}")


bot = CCBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info("Conectado como %s | ID %s", bot.user, bot.user.id)
    await send_notice(f"CC BOT conectado: {bot.user}")


# =====================================
# PRODUTOS
# Comandos: /produto criar, editar,
# excluir e listar
# =====================================

produto = app_commands.Group(
    name="produto",
    description="Criar e administrar produtos"
)


@produto.command(name="criar", description="Criar e publicar um produto")
@app_commands.check(authorized)
@app_commands.describe(
    nome="Nome do produto",
    descricao="Descrição que aparecerá na publicação",
    preco="Preço do produto",
    canal="Canal onde o produto será publicado",
    imagem="URL opcional da imagem",
    link="Link opcional para acessar ou comprar"
)
async def produto_criar(
    interaction: discord.Interaction,
    nome: str,
    descricao: str,
    preco: float,
    canal: discord.TextChannel,
    imagem: str = None,
    link: str = None,
):
    global products

    if preco < 0:
        await interaction.response.send_message(
            "O preço não pode ser negativo.", ephemeral=True
        )
        return

    if imagem and not imagem.startswith(("https://", "http://")):
        await interaction.response.send_message(
            "A imagem precisa ser uma URL válida.", ephemeral=True
        )
        return

    if link and not link.startswith(("https://", "http://")):
        await interaction.response.send_message(
            "O link precisa começar com http:// ou https://.",
            ephemeral=True
        )
        return

    product_id = max(
        (p.get("id", 0) for p in products),
        default=0
    ) + 1

    embed = discord.Embed(
        title=nome,
        description=descricao,
        color=discord.Color.dark_grey()
    )
    embed.add_field(
        name="Preço",
        value=f"R$ {preco:.2f}".replace(".", ","),
        inline=True
    )
    embed.add_field(name="Código", value=str(product_id), inline=True)

    if imagem:
        embed.set_thumbnail(url=imagem)

    embed.set_footer(text="CC BOT • Produtos")

    view = None
    if link:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Entrar",
            style=discord.ButtonStyle.link,
            url=link
        ))

    try:
        message = await canal.send(embed=embed, view=view)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Não tenho permissão para publicar nesse canal.",
            ephemeral=True
        )
        return
    except discord.HTTPException as exc:
        await interaction.response.send_message(
            f"Falha ao publicar o produto: {exc}",
            ephemeral=True
        )
        return

    products.append({
        "id": product_id,
        "nome": nome,
        "descricao": descricao,
        "preco": preco,
        "imagem": imagem,
        "link": link,
        "canal_id": canal.id,
        "mensagem_id": message.id,
        "criado_em": datetime.now().isoformat(timespec="seconds"),
    })
    write_json(PRODUCTS_FILE, products)

    await interaction.response.send_message(
        f"Produto **{nome}** criado e publicado em {canal.mention}.\n"
        f"Código do produto: `{product_id}`",
        ephemeral=True
    )


@produto.command(name="listar", description="Listar produtos cadastrados")
@app_commands.check(authorized)
async def produto_listar(interaction: discord.Interaction):
    if not products:
        await interaction.response.send_message(
            "Nenhum produto cadastrado.", ephemeral=True
        )
        return

    lines = [
        f"`{p['id']}` — **{p['nome']}** — "
        f"R$ {p['preco']:.2f}".replace(".", ",")
        for p in products[:25]
    ]

    embed = discord.Embed(
        title="Produtos cadastrados",
        description="\n".join(lines),
        color=discord.Color.dark_grey()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@produto.command(name="editar", description="Editar um produto cadastrado")
@app_commands.check(authorized)
@app_commands.describe(
    codigo="Código do produto",
    nome="Novo nome (opcional)",
    descricao="Nova descrição (opcional)",
    preco="Novo preço (opcional)",
    imagem="Nova URL de imagem (opcional)",
    link="Novo link (opcional)"
)
async def produto_editar(
    interaction: discord.Interaction,
    codigo: int,
    nome: str = None,
    descricao: str = None,
    preco: float = None,
    imagem: str = None,
    link: str = None,
):
    global products

    item = next((p for p in products if p["id"] == codigo), None)
    if item is None:
        await interaction.response.send_message(
            "Produto não encontrado.", ephemeral=True
        )
        return

    if preco is not None and preco < 0:
        await interaction.response.send_message(
            "O preço não pode ser negativo.", ephemeral=True
        )
        return

    for key, value in (
        ("nome", nome),
        ("descricao", descricao),
        ("preco", preco),
        ("imagem", imagem),
        ("link", link),
    ):
        if value is not None:
            item[key] = value

    write_json(PRODUCTS_FILE, products)

    # Atualiza a publicação original, quando ela ainda existe.
    try:
        channel = bot.get_channel(item["canal_id"])
        if channel is None:
            channel = await bot.fetch_channel(item["canal_id"])

        message = await channel.fetch_message(item["mensagem_id"])

        embed = discord.Embed(
            title=item["nome"],
            description=item["descricao"],
            color=discord.Color.dark_grey()
        )
        embed.add_field(
            name="Preço",
            value=f"R$ {item['preco']:.2f}".replace(".", ","),
            inline=True
        )
        embed.add_field(name="Código", value=str(item["id"]), inline=True)

        if item.get("imagem"):
            embed.set_thumbnail(url=item["imagem"])

        embed.set_footer(text="CC BOT • Produtos")

        view = None
        if item.get("link"):
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Entrar",
                style=discord.ButtonStyle.link,
                url=item["link"]
            ))

        await message.edit(embed=embed, view=view)

    except (discord.HTTPException, KeyError):
        await interaction.response.send_message(
            "Dados salvos, mas não consegui atualizar a publicação original.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"Produto `{codigo}` atualizado.", ephemeral=True
    )


@produto.command(name="excluir", description="Excluir um produto cadastrado")
@app_commands.check(authorized)
@app_commands.describe(codigo="Código do produto")
async def produto_excluir(
    interaction: discord.Interaction,
    codigo: int
):
    global products

    item = next((p for p in products if p["id"] == codigo), None)
    if item is None:
        await interaction.response.send_message(
            "Produto não encontrado.", ephemeral=True
        )
        return

    # Remove também a publicação, se possível.
    try:
        channel = bot.get_channel(item["canal_id"])
        if channel is None:
            channel = await bot.fetch_channel(item["canal_id"])
        message = await channel.fetch_message(item["mensagem_id"])
        await message.delete()
    except (discord.HTTPException, KeyError):
        pass

    products = [p for p in products if p["id"] != codigo]
    write_json(PRODUCTS_FILE, products)

    await interaction.response.send_message(
        f"Produto `{codigo}` excluído do cadastro.",
        ephemeral=True
    )


bot.tree.add_command(produto)


# =====================================
# CONFIGURAÇÃO DE VENDAS
# =====================================

vendas = app_commands.Group(
    name="vendas",
    description="Configurações do sistema de vendas"
)


@vendas.command(
    name="configurar",
    description="Definir o canal de avisos de vendas"
)
@app_commands.check(authorized)
async def vendas_configurar(
    interaction: discord.Interaction,
    canal: discord.TextChannel
):
    config["sales_channel"] = canal.id
    write_json(CONFIG_FILE, config)

    await interaction.response.send_message(
        f"Canal de vendas configurado: {canal.mention}",
        ephemeral=True
    )


bot.tree.add_command(vendas)


# =====================================
# CONFIGURAÇÃO DE BOAS-VINDAS
# =====================================

boasvindas = app_commands.Group(
    name="boasvindas",
    description="Configurar mensagens de boas-vindas"
)


@boasvindas.command(
    name="configurar",
    description="Definir canal e mensagem de boas-vindas"
)
@app_commands.check(authorized)
@app_commands.describe(
    canal="Canal para receber novos membros",
    mensagem="Use {user} para mencionar o novo membro"
)
async def boasvindas_configurar(
    interaction: discord.Interaction,
    canal: discord.TextChannel,
    mensagem: str
):
    config["welcome_channel"] = canal.id
    config["welcome_message"] = mensagem
    write_json(CONFIG_FILE, config)

    await interaction.response.send_message(
        f"Boas-vindas configuradas em {canal.mention}.",
        ephemeral=True
    )


bot.tree.add_command(boasvindas)


@bot.event
async def on_member_join(member: discord.Member):
    channel_id = config.get("welcome_channel")
    if not channel_id:
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        return

    message = config.get("welcome_message", "Bem-vindo(a), {user}!")
    message = message.replace("{user}", member.mention)

    try:
        await channel.send(message)
    except discord.HTTPException:
        log.exception("Falha ao enviar boas-vindas.")


# =====================================
# SAMPLES — COMANDO INICIAL
# =====================================

samples = app_commands.Group(
    name="samples",
    description="Ferramentas para geração de samples"
)


@samples.command(
    name="gerar",
    description="Solicitar geração de samples"
)
@app_commands.check(authorized)
@app_commands.describe(
    quantidade="Quantidade desejada, de 1 a 100",
    estilo="Estilo de sample desejado"
)
async def samples_gerar(
    interaction: discord.Interaction,
    quantidade: app_commands.Range[int, 1, 100],
    estilo: str
):
    await interaction.response.send_message(
        "O comando foi reconhecido, mas o mecanismo de síntese "
        "e geração dos arquivos ainda precisa ser implementado.",
        ephemeral=True
    )


bot.tree.add_command(samples)


# =====================================
# ERROS E INICIALIZAÇÃO
# =====================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.CheckFailure):
        # Acesso não autorizado: nenhuma resposta.
        return

    log.exception("Erro em comando slash: %s", error)

    if interaction.response.is_done():
        return

    await interaction.response.send_message(
        "Ocorreu um erro ao executar o comando.",
        ephemeral=True
    )


async def main():
    if not TOKEN_FILE.is_file():
        print(f"Token não encontrado: {TOKEN_FILE}")
        return

    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        print("O arquivo token.txt está vazio.")
        return

    try:
        async with bot:
            await bot.start(token)
    except discord.LoginFailure:
        print("Token inválido. Confira token.txt.")
    except discord.PrivilegedIntentsRequired:
        print("Ative os intents necessários no Developer Portal.")
    except Exception as exc:
        log.exception("Falha ao iniciar o CC BOT: %s", exc)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("CC BOT encerrado.")
