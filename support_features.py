import re
import discord

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
            if m.author.id == bot.user.id and m.embeds and "suporte dedicado" in (m.embeds[0].title or "").casefold():
                msg = m
                break
        if msg:
            await msg.edit(embed=criar_embed_suporte(), view=SupportPanelView())
        else:
            await ch.send(embed=criar_embed_suporte(), view=SupportPanelView())

    return SupportPanelView, TicketCloseView, criar_embed_suporte, garantir_painel_suporte
