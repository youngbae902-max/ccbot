import re
import discord

SUPPORT_INSTAGRAM_URL = ""

def configure_support(bot, admin_id, whatsapp_number, is_admin, log):
    class TicketCloseView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="Encerrar atendimento", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="ccbot:support:close")
        async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
            ch = interaction.channel
            if not isinstance(ch, discord.TextChannel) or not ch.topic or "ticket_owner_id=" not in ch.topic:
                await interaction.response.send_message("Ticket inválido.", ephemeral=True)
                return
            try:
                owner_id = int(ch.topic.split("ticket_owner_id=", 1)[1].split()[0])
            except (ValueError, IndexError):
                owner_id = 0
            if interaction.user.id != owner_id and not is_admin(interaction) and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("Só quem abriu o ticket ou a administração pode encerrar.", ephemeral=True)
                return
            await interaction.response.send_message("🔒 Atendimento encerrado.")
            owner = interaction.guild.get_member(owner_id) if interaction.guild else None
            if owner:
                await ch.set_permissions(owner, send_messages=False)
            if not ch.name.startswith("fechado-"):
                await ch.edit(name=("fechado-" + ch.name)[:100])

    class SupportPanelView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="Chamar ADM", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="ccbot:support:open")
        async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message("Use no servidor.", ephemeral=True)
                return
            current = next((c for c in guild.text_channels if c.topic and f"ticket_owner_id={interaction.user.id}" in c.topic and not c.name.startswith("fechado-")), None)
            if current:
                await interaction.response.send_message(f"Seu ticket já está aberto: {current.mention}", ephemeral=True)
                return
            cat = discord.utils.get(guild.categories, name="SUPORTE • TICKETS")
            if cat is None:
                cat = await guild.create_category("SUPORTE • TICKETS", overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)})
            ow = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
            }
            if guild.me:
                ow[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_messages=True)
            admin = guild.get_member(admin_id)
            if admin:
                ow[admin] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
            name = re.sub(r"[^a-z0-9-]", "", interaction.user.name.casefold().replace(" ", "-"))[:35] or "usuario"
            ticket = await guild.create_text_channel(f"ticket-{name}-{str(interaction.user.id)[-4:]}", category=cat, overwrites=ow, topic=f"ticket_owner_id={interaction.user.id}")
            em = discord.Embed(
                title="🎫 𝗔𝗧𝗘𝗡𝗗𝗜𝗠𝗘𝗡𝗧𝗢 𝗔𝗕𝗘𝗥𝗧𝗢",
                description=f"Olá, {interaction.user.mention}. Conte aqui o que aconteceu.\n\n**Atendimento humano**\nA equipe busca responder em até **24 horas**. O bot organiza o canal; uma pessoa vai atender você.\n\nNão envie senhas nem dados bancários completos. Quando resolver, use **Encerrar atendimento**.",
                color=discord.Color.from_rgb(18, 18, 22)
            )
            await ticket.send(content=f"{interaction.user.mention} <@{admin_id}>", embed=em, view=TicketCloseView())
            await interaction.response.send_message(f"Seu ticket foi aberto: {ticket.mention}", ephemeral=True)

    def criar_embed_suporte():
        em = discord.Embed(
            title="🎧 𝗦𝗨𝗣𝗢𝗥𝗧𝗘 𝗗𝗘𝗗𝗜𝗖𝗔𝗗𝗢",
            description=f"**Precisou de ajuda? Vamos resolver com você.**\n-# ATENDIMENTO HUMANO • C.C ©\n\nAbra um ticket privado para falar com a administração. Respondemos em até **24 horas**. O atendimento é feito por pessoas, não por respostas automáticas.\n\n**📲 CONTATO DIRETO**\nWhatsApp: [falar com o C.C ©](https://wa.me/{whatsapp_number})",
            color=discord.Color.from_rgb(18, 18, 22)
        )
        if SUPPORT_INSTAGRAM_URL:
            em.add_field(name="📸 INSTAGRAM", value=f"[Acessar perfil oficial]({SUPPORT_INSTAGRAM_URL})", inline=False)
        em.add_field(name="✦ COMO FUNCIONA", value="Toque em **Chamar ADM**, descreva seu problema e aguarde no ticket. Você e a administração terão acesso ao canal.", inline=False)
        em.set_footer(text="C.C © • Suporte privado")
        return em

    async def garantir_painel_suporte(guild):
        ch = next((c for c in guild.text_channels if "suporte" in c.name.casefold() and ("dedicado" in c.name.casefold() or c.name.casefold() == "suporte")), None)
        if ch is None:
            log.warning("Canal de suporte dedicado não encontrado.")
            return
        msg = None
        async for m in ch.history(limit=50):
            if m.author.id == bot.user.id and m.embeds and "suporte" in (m.embeds[0].title or "").casefold():
                msg = m
                break
        if msg:
            await msg.edit(embed=criar_embed_suporte(), view=SupportPanelView())
        else:
            await ch.send(embed=criar_embed_suporte(), view=SupportPanelView())


    async def garantir_regras(guild):
        if guild is None:
            return
        ch = guild.get_channel(1561513274737168414)
        if not isinstance(ch, discord.TextChannel):
            try:
                ch = await guild.fetch_channel(1561513274737168414)
            except discord.HTTPException:
                log.warning("Canal de regras não encontrado.")
                return
        em = discord.Embed(
            title="📜 𝗥𝗘𝗚𝗥𝗔𝗦 𝗗𝗢 𝗖.𝗖 ©",
            description=(
                "**Uma comunidade criativa, segura e transparente.**\n-# LEIA ANTES DE PARTICIPAR OU COMPRAR\n\n"
                "**01 · Respeito**\nRacismo, preconceito, assédio, ameaças e discriminação não serão tolerados. Racismo e preconceito geram **banimento permanente**.\n\n"
                "**02 · Conteúdo proibido**\nÉ proibido divulgar bets/apostas, pornografia, conteúdo sexual explícito/adulto, golpes, links maliciosos ou conteúdo ilegal.\n\n"
                "**03 · Divulgação**\nO canal de divulgações é exclusivo para **beats**. Não divulgue lojas, grupos, servidores, serviços ou produtos fora dos espaços autorizados. Spam pode ser removido.\n\n"
                "**04 · Compras**\nAntes de pagar, leia **como funcionam as vendas**, confira produto, preço e condições. O pagamento é conferido manualmente; comprovante não significa aprovação automática. Não abra pedidos duplicados.\n\n"
                "**05 · Golpes**\nTentativa de golpe, fraude, comprovante falso ou tentativa de enganar clientes/equipe pode resultar em **banimento imediato**. Nunca compartilhe senhas ou dados bancários completos.\n\n"
                "**06 · Suporte**\nSe houver problema com compra, entrega ou acesso, abra um ticket no **Suporte Dedicado**. A equipe busca responder em até **24 horas** e também oferece contato por WhatsApp.\n\n"
                "**07 · Materiais**\nRespeite as condições de cada produto e material gratuito. Não revenda ou redistribua conteúdo contrariando as condições informadas.\n\n"
                "**08 · Moderação**\nAs regras valem para todos, inclusive clientes. A equipe pode remover conteúdo e aplicar advertência, restrição ou banimento conforme a gravidade.\n\n"
                "**09 · Segurança nas vendas**\nMantenha a conversa no canal privado do pedido. Se algo parecer estranho, pare e fale com o suporte antes de continuar.\n\n"
                "*Ao permanecer no servidor, você concorda em seguir estas regras.*"
            ),
            color=discord.Color.from_rgb(18, 18, 22)
        )
        em.set_footer(text="C.C © • Respeito, segurança e transparência")
        old = None
        async for msg in ch.history(limit=50):
            if msg.author.id == bot.user.id and msg.embeds and "regras" in (msg.embeds[0].title or "").casefold():
                old = msg
                break
        if old:
            await old.edit(embed=em)
        else:
            await ch.send(embed=em)

    async def garantir_canal_divulgacoes(guild):
        if guild is None:
            return
        ch = next((x for x in guild.text_channels if "divulg" in x.name.casefold()), None)
        if ch is None:
            log.warning("Canal de divulgações não encontrado.")
            return
        content = (
            "# <:tag:1561787923865665667> DIVULGAÇÕES DE BEATS\n-# ESPAÇO EXCLUSIVO PARA PRODUTORES\n\n"
            "Use este canal para divulgar **somente beats**: produções, previews, instrumentais e links diretos para ouvir ou comprar seus beats.\n\n"
            "**O que pode:**\n• Beats autorais e previews musicais.\n• Links relacionados diretamente ao beat.\n• Estilo, BPM e contato profissional.\n\n"
            "**O que não pode:**\n• Divulgação de servidores, grupos, lojas, serviços ou produtos que não sejam beats.\n• Bets/apostas, pornografia, conteúdo adulto, racismo, preconceito, golpes ou spam.\n\n"
            "Mantenha as publicações organizadas. Conteúdo proibido será removido e infrações graves podem resultar em banimento."
        )
        old = None
        async for msg in ch.history(limit=50):
            if msg.author.id == bot.user.id and ("DIVULG" in (msg.content or "").upper() or (msg.embeds and "divulg" in (msg.embeds[0].title or "").casefold())):
                old = msg
                break
        if old:
            await old.edit(content=content, embed=None, view=None)
        else:
            await ch.send(content)

    return SupportPanelView, TicketCloseView, criar_embed_suporte, garantir_painel_suporte, garantir_regras, garantir_canal_divulgacoes
