import discord


def configure_promos(bot, channels, urls, settings, data_file, save_json, log):
    class PromoLinksView(discord.ui.View):
        def __init__(self, label: str, url: str):
            super().__init__(timeout=None)
            emoji = "🛒" if "Loja" in label else "🎛️" if "PACKy" in label else "🧭"
            self.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=url, emoji=emoji))

    def criar_embed_promocional(nome: str) -> discord.Embed:
        estilos = {
            "Loja CC": ("🛒 𝗟𝗢𝗝𝗔 𝗖𝗖", "SEU PRÓXIMO ACHADO COMEÇA AQUI", "Encontre os produtos e serviços do C.C © em um só lugar, com acesso direto à loja oficial.", "✦ EXPLORE A LOJA", "Confira as opções disponíveis e leia as informações de cada produto antes de comprar."),
            "PACKy": ("<:box:1561761831587160140> 𝗣𝗔𝗖𝗞𝘆", "MATERIAL PARA ALIMENTAR SUA CRIATIVIDADE", "Explore packs e materiais para quem produz música e quer ampliar suas possibilidades.", "✦ ENCONTRE SEU MATERIAL", "Veja os conteúdos disponíveis e escolha o que combina com seu processo."),
            "Garimpo": ("🧭 𝗚𝗔𝗥𝗜𝗠𝗣𝗢", "DESCOBERTAS, OPORTUNIDADES E NOVIDADES", "Acesse o Garimpo para encontrar novidades e acompanhar os conteúdos reunidos por lá.", "✦ ENTRE PARA GARIMPAR", "Abra o perfil oficial e explore o que está disponível no momento."),
        }
        titulo, linha, texto, campo, detalhe = estilos[nome]
        embed = discord.Embed(
            title=titulo,
            description=f"**{linha}**\n-# ACESSO OFICIAL • C.C ©\n\n{texto}",
            color=discord.Color.from_rgb(14, 14, 18)
        )
        embed.add_field(name=campo, value=detalhe, inline=False)
        embed.add_field(name="↗ ACESSO DIRETO", value="Use o botão abaixo para abrir a página oficial.", inline=False)
        guild = bot.get_guild(1561513043886743562)
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.set_footer(text="C.C © • Links oficiais | confira os detalhes antes de comprar")
        return embed

    async def publicar_embeds_promocionais():
        publicados = settings.get("promo_embeds", {})
        for nome, canal_id in channels.items():
            registro = publicados.get(nome, {})
            mensagem_id = registro.get("message_id") if isinstance(registro, dict) else None
            try:
                canal = bot.get_channel(canal_id) or await bot.fetch_channel(canal_id)
                mensagem = None
                if mensagem_id:
                    try:
                        mensagem = await canal.fetch_message(int(mensagem_id))
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        mensagem = None
                if mensagem is None:
                    async for candidata in canal.history(limit=50):
                        if candidata.author.id == bot.user.id and candidata.embeds:
                            mensagem = candidata
                            break
                if mensagem is None:
                    log.warning("Painel promocional de %s não encontrado; não será criada cópia.", nome)
                    continue
                rotulos = {"Loja CC": "Abrir Loja CC", "PACKy": "Explorar PACKy", "Garimpo": "Abrir Garimpo"}
                await mensagem.edit(embed=criar_embed_promocional(nome), view=PromoLinksView(rotulos[nome], urls[nome]))
                publicados[nome] = {"channel_id": canal_id, "message_id": mensagem.id}
                settings["promo_embeds"] = publicados
                save_json(data_file, settings)
                log.info("Painel promocional atualizado: %s / mensagem %s", nome, mensagem.id)
            except Exception:
                log.exception("Falha ao atualizar embed promocional: %s", nome)

    return PromoLinksView, criar_embed_promocional, publicar_embeds_promocionais
