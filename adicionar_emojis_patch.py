from pathlib import Path

p = Path("ccbot.py")
c = p.read_text(encoding="utf-8")

if 'name="adicionar_emojis"' in c:
    print("O comando /adicionar_emojis já existe.")
    raise SystemExit(0)

marker = '\n@bot.tree.command(name="samples", description="Publicar o painel do Gerador de Samples")'
if marker not in c:
    raise SystemExit("Não encontrei o ponto de inserção. Arquivo não alterado.")

codigo = r'''

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
'''
p.write_text(c.replace(marker, "\n" + codigo + marker), encoding="utf-8")
print("Comando /adicionar_emojis inserido.")
