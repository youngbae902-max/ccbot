import discord


def criar_embed_painel_gratis(cfg, kind):
    tipo = "packs" if kind == "packs" else "projetos"
    foco = (
        "Descubra sons, texturas e ideias para experimentar em novas produções."
        if kind == "packs"
        else "Explore projetos, estruturas e sessões que ajudam a estudar organização e fluxo de produção."
    )
    embed = discord.Embed(
        title=cfg["title"],
        description=(
            f"**Conteúdo gratuito para sua próxima ideia.**\n"
            "-# PAINEL ATUALIZADO • C.C ©\n\n"
            f"{cfg['description']}\n\n"
            "**COMO ACESSAR**\n"
            "Toque no botão abaixo. O bot abrirá um tópico privado só seu, para manter as orientações e dúvidas organizadas."
        ),
        color=discord.Color.from_rgb(18, 18, 22)
    )
    embed.add_field(name="✦ O QUE VOCÊ ENCONTRA", value=foco, inline=False)
    embed.add_field(
        name="✦ UM ESPAÇO SÓ SEU",
        value=f"Use o tópico privado para consultar os materiais de {tipo}, tirar dúvidas e manter a conversa no lugar certo.",
        inline=False
    )
    embed.set_footer(text="C.C © • Conteúdo gratuito | acesso privado")
    return embed
