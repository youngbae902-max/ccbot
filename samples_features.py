import io
import json
import os
import re
import uuid
import zipfile
import logging
from pathlib import Path
from datetime import datetime

import discord

log = logging.getLogger("CCBOT.samples")

SAMPLE_TYPES = {
    "continuas": "Samples contínuas",
    "packs": "Samples de outros packs",
    "ia": "Samples feitas por IA",
}
MAX_UPLOAD_FILES = 10
MAX_ITEM_BYTES = 24 * 1024 * 1024
MAX_EXPORT_BYTES = 24 * 1024 * 1024


def _safe_name(value):
    name = Path(str(value).replace("\\", "/")).name
    name = re.sub(r"[^\w.() -]", "_", name, flags=re.UNICODE).strip(" .")
    return (name or "arquivo")[:180]


def _slug(value):
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value)).strip("-").lower()
    return (value or "dj")[:70]


class SamplesSystem:
    def __init__(self, bot, config_dir, djs_file, admin_id):
        self.bot = bot
        self.config_dir = Path(config_dir)
        self.djs_file = Path(djs_file)
        self.admin_id = int(admin_id)
        self.stock_file = self.config_dir / "samples_stock.json"
        self.history_file = self.config_dir / "samples_history.json"
        self.types_file = self.config_dir / "samples_stock_types.json"
        self.files_dir = self.config_dir / "samples_stock_files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.stock = self._load(self.stock_file, {})
        self.history = self._load(self.history_file, {})
        self.stock_types = self._load(self.types_file, {})
        if not isinstance(self.stock_types, dict):
            self.stock_types = {}
        self._migrate_stock_types()
        if not isinstance(self.stock, dict):
            self.stock = {}
        if not isinstance(self.history, dict):
            self.history = {}

    def _load(self, path, default):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data
        except FileNotFoundError:
            self._save(path, default)
            return default
        except (OSError, json.JSONDecodeError):
            log.exception("Falha ao carregar %s", path)
            return default

    def _save(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)

    def save_stock(self):
        self._save(self.stock_file, self.stock)

    def save_history(self):
        self._save(self.history_file, self.history)

    def djs(self):
        try:
            values = json.loads(self.djs_file.read_text(encoding="utf-8"))
            return [str(x) for x in values if str(x).strip()] if isinstance(values, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _migrate_stock_types(self):
        # Mantém estoques existentes, sem criar os três padrões para todo DJ.
        changed = False
        for key in self.stock:
            parts = key.rsplit("::", 1)
            if len(parts) != 2:
                continue
            dj, category = parts
            types = self.stock_types.setdefault(dj, [])
            if not any(isinstance(x, dict) and x.get("id") == category for x in types):
                types.append({"id": category, "name": SAMPLE_TYPES.get(category, category.replace("_", " ").replace("-", " ").title())})
                changed = True
        if changed:
            self.save_stock_types()

    def save_stock_types(self):
        self._save(self.types_file, self.stock_types)

    def stock_categories(self, dj):
        return [x for x in self.stock_types.get(dj, []) if isinstance(x, dict) and x.get("id") and x.get("name")]

    def stock_label(self, dj, category):
        item = next((x for x in self.stock_categories(dj) if x["id"] == category), None)
        return item["name"] if item else SAMPLE_TYPES.get(category, str(category))

    def add_stock_type(self, dj, name):
        name = str(name).strip()
        if not name:
            raise ValueError("Digite um nome para o stock.")
        if len(name) > 90:
            raise ValueError("O nome deve ter até 90 caracteres.")
        types = self.stock_types.setdefault(dj, [])
        if len(types) >= 25:
            raise ValueError("Cada DJ pode ter até 25 stocks.")
        if any(str(x.get("name", "")).casefold() == name.casefold() for x in types if isinstance(x, dict)):
            raise ValueError("Esse DJ já tem um stock com esse nome.")
        category = "custom_" + uuid.uuid4().hex[:12]
        types.append({"id": category, "name": name})
        self.save_stock_types()
        self.stock.setdefault(self._key(dj, category), [])
        self.save_stock()
        return category

    def rename_stock_type(self, dj, category, name):
        name = str(name).strip()
        if not name:
            raise ValueError("Digite um nome para o stock.")
        if len(name) > 90:
            raise ValueError("O nome deve ter até 90 caracteres.")
        types = self.stock_types.get(dj, [])
        target = next((x for x in types if isinstance(x, dict) and x.get("id") == category), None)
        if target is None:
            raise ValueError("Esse stock não existe mais.")
        if any(x is not target and str(x.get("name", "")).casefold() == name.casefold() for x in types if isinstance(x, dict)):
            raise ValueError("Esse DJ já tem um stock com esse nome.")
        target["name"] = name
        self.save_stock_types()

    def _key(self, dj, category):
        return f"{dj}::{category}"

    def items(self, dj, category):
        value = self.stock.get(self._key(dj, category), [])
        return value if isinstance(value, list) else []

    def _item_path(self, item):
        raw = item.get("path", "")
        path = (self.files_dir / raw).resolve()
        root = self.files_dir.resolve()
        if root not in path.parents:
            raise ValueError("Caminho de arquivo inválido no estoque.")
        return path

    def is_admin(self, interaction):
        return interaction.user.id == self.admin_id

    def add_item(self, dj, category, item):
        self.stock.setdefault(self._key(dj, category), []).append(item)
        self.save_stock()

    def remove_item_file(self, item):
        if item.get("kind") not in ("file", "zip") or not item.get("path"):
            return
        try:
            path = self._item_path(item)
            path.unlink(missing_ok=True)
        except (OSError, ValueError):
            log.exception("Não consegui remover arquivo do estoque: %s", item.get("name"))

    def rename_dj(self, old, new):
        prefix = old + "::"
        for old_key in list(self.stock):
            if old_key.startswith(prefix):
                category = old_key[len(prefix):]
                self.stock[self._key(new, category)] = self.stock.pop(old_key)
        self.save_stock()
        if old in self.stock_types:
            self.stock_types[new] = self.stock_types.pop(old)
            self.save_stock_types()
        # Migra as chaves de histórico sem apagar entregas anteriores.
        for key, sent in list(self.history.items()):
            parts = key.split("::", 2)
            if len(parts) == 3 and parts[1] == old:
                self.history[f"{parts[0]}::{new}::{parts[2]}"] = sent
                del self.history[key]
        self.save_history()

    def stock_summary(self, dj, category):
        items = self.items(dj, category)
        return f"{len(items)} item(ns) no estoque"

    def _history_key(self, user_id, dj, category):
        return f"{user_id}::{dj}::{category}"

    async def deliver(self, interaction, dj, category, quantity):
        items = self.items(dj, category)
        hist_key = self._history_key(interaction.user.id, dj, category)
        already = set(self.history.get(hist_key, []))
        available = [item for item in items if item.get("id") not in already]
        if not available:
            await interaction.followup.send(
                "Você já recebeu todos os itens disponíveis desse DJ e tipo. Não há novos itens para enviar agora.",
                ephemeral=True
            )
            return
        chosen = available[:quantity]
        files = [x for x in chosen if x.get("kind") in ("file", "zip")]
        links = [x for x in chosen if x.get("kind") == "link"]
        sent_ids = []
        notes = []

        def record_sent(item_ids):
            nonlocal already
            item_ids = list(item_ids)
            if not item_ids:
                return
            sent_ids.extend(item_ids)
            already = already | set(item_ids)
            self.history[hist_key] = list(already)
            self.save_history()

        try:
            # Links vão em texto; arquivos soltos são agrupados em ZIP; ZIPs originais ficam intactos.
            if links:
                text = "\n".join(f"• **{discord.utils.escape_markdown(x.get('name', 'Link'))}**\n{x.get('url', '')}" for x in links)
                await interaction.user.send("**🎧 Seus links de samples**\n" + text)
                record_sent(x["id"] for x in links)

            original_zips = [x for x in files if x.get("kind") == "zip"]
            loose = [x for x in files if x.get("kind") == "file"]
            for item in original_zips:
                path = self._item_path(item)
                data = await __import__("asyncio").to_thread(path.read_bytes)
                if len(data) > MAX_ITEM_BYTES:
                    notes.append(f"{item.get('name')}: arquivo acima do limite de envio do Discord; não enviado")
                    continue
                await interaction.user.send(
                    content=f"📦 **{discord.utils.escape_markdown(item.get('name', 'Arquivo ZIP'))}**",
                    file=discord.File(io.BytesIO(data), filename=_safe_name(item.get("name", "samples.zip")))
                )
                record_sent([item["id"]])

            if loose:
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    for item in loose:
                        path = self._item_path(item)
                        data = await __import__("asyncio").to_thread(path.read_bytes)
                        archive.writestr(_safe_name(item.get("name", path.name)), data)
                data = buffer.getvalue()
                if len(data) <= MAX_ITEM_BYTES:
                    await interaction.user.send(
                        content="🎧 **Suas samples em arquivos agrupados**",
                        file=discord.File(io.BytesIO(data), filename=f"samples_{_slug(dj)}_{category}.zip")
                    )
                    record_sent(x["id"] for x in loose)
                else:
                    # Envia arquivos pequenos em lotes; arquivos acima do limite ficam pendentes.
                    for item in loose:
                        path = self._item_path(item)
                        data = await __import__("asyncio").to_thread(path.read_bytes)
                        if len(data) > MAX_ITEM_BYTES:
                            notes.append(f"{item.get('name')}: arquivo acima do limite de envio; não enviado")
                            continue
                        await interaction.user.send(
                            file=discord.File(io.BytesIO(data), filename=_safe_name(item.get("name", path.name)))
                        )
                        record_sent([item["id"]])

            if sent_ids:
                self.history[hist_key] = list(already | set(sent_ids))
                self.save_history()
            detail = f"Entregues: **{len(sent_ids)}** de **{quantity}** solicitadas."
            if len(chosen) < quantity:
                detail += f"\nSó havia **{len(chosen)}** item(ns) novo(s) disponível(is) para você."
            if notes:
                detail += "\n\nNão enviados:\n" + "\n".join("• " + n for n in notes[:8])
            await interaction.followup.send(detail, ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                "Não consegui enviar no PV. Ative as mensagens diretas deste servidor e tente novamente. O histórico não foi atualizado para os itens que não consegui entregar.",
                ephemeral=True
            )
        except Exception:
            log.exception("Falha ao entregar estoque de samples")
            await interaction.followup.send(
                "Ocorreu um erro durante a entrega. Confira o PV e tente novamente; avise o administrador se algum item chegou incompleto.",
                ephemeral=True
            )

    def export_txt(self, dj, category):
        lines = [
            "# CC BOT — Exportação do estoque de samples",
            f"DJ: {dj}",
            f"Tipo: {self.stock_label(dj, category)}",
            f"Exportado em: {datetime.now().isoformat(timespec='seconds')}",
            "# Cada item começa com ITEM; tipo=file/zip/link; nome=...; link=... (quando for link)",
            ""
        ]
        for item in self.items(dj, category):
            lines.append(
                "ITEM;id={};tipo={};nome={};link={}".format(
                    item.get("id", ""),
                    item.get("kind", ""),
                    str(item.get("name", "")).replace("\n", " "),
                    str(item.get("url", "")).replace("\n", " ")
                )
            )
        return ("\n".join(lines) + "\n").encode("utf-8")

    def export_zip(self, dj, category):
        buffer = io.BytesIO()
        manifest = {"dj": dj, "category": category, "type_label": self.stock_label(dj, category), "items": []}
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in self.items(dj, category):
                record = {k: v for k, v in item.items() if k != "path"}
                if item.get("kind") in ("file", "zip"):
                    path = self._item_path(item)
                    record["archive_path"] = f"files/{item.get('id')}_{_safe_name(item.get('name', path.name))}"
                    archive.write(path, record["archive_path"])
                manifest["items"].append(record)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        return buffer.getvalue()

    async def admin_export(self, interaction, dj, category, kind):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = self.export_txt(dj, category) if kind == "txt" else self.export_zip(dj, category)
            if len(data) > MAX_ITEM_BYTES:
                await interaction.followup.send(
                    "A exportação ultrapassa o limite de anexo do Discord. Divida o estoque em lotes menores.",
                    ephemeral=True
                )
                return
            filename = f"estoque_{_slug(dj)}_{category}.{kind}"
            await interaction.followup.send(
                f"Exportação do estoque de **{dj}** — **{self.stock_label(dj, category)}**.",
                file=discord.File(io.BytesIO(data), filename=filename),
                ephemeral=True
            )
        except Exception:
            log.exception("Falha ao exportar estoque")
            await interaction.followup.send("Não consegui exportar esse estoque.", ephemeral=True)

    async def import_replace(self, interaction, dj, category, attachment):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if attachment.size > MAX_ITEM_BYTES:
                raise ValueError("O arquivo ultrapassa o limite de 24 MB.")
            data = await attachment.read(use_cached=True)
            incoming = []
            if Path(attachment.filename).suffix.lower() == ".zip":
                with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
                    manifest_info = next((x for x in archive.infolist() if x.filename == "manifest.json"), None)
                    if manifest_info is None:
                        raise ValueError("O ZIP precisa ser uma exportação do estoque feita pelo CC BOT.")
                    manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
                    for item in manifest.get("items", []):
                        kind = item.get("kind")
                        if kind == "link":
                            incoming.append({"id": uuid.uuid4().hex, "kind": "link", "name": _safe_name(item.get("name", "Link")), "url": item.get("url", "")})
                        elif kind in ("file", "zip"):
                            archive_path = item.get("archive_path", "")
                            info = next((x for x in archive.infolist() if x.filename == archive_path and not x.is_dir()), None)
                            if info is None:
                                continue
                            content = archive.read(info)
                            if len(content) > MAX_ITEM_BYTES:
                                continue
                            item_id = uuid.uuid4().hex
                            name = _safe_name(item.get("name", "arquivo"))
                            dest_name = f"{_slug(dj)}/{category}/{item_id}_{name}"
                            dest = (self.files_dir / dest_name).resolve()
                            if self.files_dir.resolve() not in dest.parents:
                                continue
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(content)
                            incoming.append({"id": item_id, "kind": kind, "name": name, "path": dest_name})
            elif Path(attachment.filename).suffix.lower() == ".txt":
                existing_by_id = {item.get("id"): item for item in self.items(dj, category)}
                for line in data.decode("utf-8-sig").splitlines():
                    if not line.startswith("ITEM;"):
                        continue
                    fields = {}
                    for part in line[5:].split(";"):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            fields[k] = v
                    item_id = fields.get("id", "")
                    kind = fields.get("tipo", "")
                    name = _safe_name(fields.get("nome", "Item"))
                    if kind == "link" and fields.get("link", "").startswith(("https://", "http://")):
                        incoming.append({"id": uuid.uuid4().hex, "kind": "link", "name": name, "url": fields["link"]})
                    elif kind in ("file", "zip") and item_id in existing_by_id:
                        preserved = dict(existing_by_id[item_id])
                        preserved["name"] = name
                        incoming.append(preserved)
            else:
                raise ValueError("Envie um .txt ou .zip exportado pelo painel.")
            old = self.items(dj, category)
            if not incoming and old:
                raise ValueError("O arquivo não contém itens válidos. Use Apagar estoque se quiser limpar tudo.")
            self.stock[self._key(dj, category)] = incoming
            self.save_stock()
            incoming_paths = {item.get("path") for item in incoming if item.get("path")}
            for old_item in old:
                if old_item.get("path") not in incoming_paths:
                    self.remove_item_file(old_item)
            await interaction.followup.send(
                f"Estoque substituído. Itens anteriores: **{len(old)}**; itens importados: **{len(incoming)}**. O histórico individual foi preservado para evitar repetição.",
                ephemeral=True
            )
        except (ValueError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            await interaction.followup.send(f"Não consegui importar: {str(exc)[:300]}", ephemeral=True)
        except Exception:
            log.exception("Falha ao importar estoque")
            await interaction.followup.send("Falha inesperada ao importar o estoque.", ephemeral=True)


SYSTEM = None


def configure_samples(bot, config_dir, djs_file, admin_id):
    global SYSTEM
    SYSTEM = SamplesSystem(bot, config_dir, djs_file, admin_id)
    return SYSTEM


class SamplesTypeSelect(discord.ui.Select):
    def __init__(self, parent, admin=False):
        self.parent_view = parent
        selected = getattr(parent, "category", None)
        dj = getattr(parent, "dj", None)
        categories = SYSTEM.stock_categories(dj) if dj else []
        options = [
            discord.SelectOption(label=str(item["name"])[:100], value=item["id"], default=item["id"] == selected)
            for item in categories
        ]
        super().__init__(
            placeholder="Qual estilo de sample você quer?",
            min_values=1, max_values=1, options=options,
            custom_id="ccbot:samples:type"
        )

    async def callback(self, interaction):
        self.parent_view.category = self.values[0]
        self.parent_view.step = "quantity"
        await self.parent_view.refresh(interaction)


class SamplesDJSelect(discord.ui.Select):
    def __init__(self, parent):
        self.parent_view = parent
        djs = SYSTEM.djs()[:25]
        options = [discord.SelectOption(label=dj[:100], value=dj, default=dj == getattr(parent, "dj", None)) for dj in djs]
        super().__init__(
            placeholder="Qual DJ você quer como inspiração?",
            min_values=1, max_values=1, options=options,
            custom_id="ccbot:samples:dj"
        )

    async def callback(self, interaction):
        self.parent_view.dj = self.values[0]
        self.parent_view.category = None
        self.parent_view.step = "type"
        await self.parent_view.refresh(interaction)


class SamplesQuantityModal(discord.ui.Modal, title="Quantidade de samples"):
    quantity = discord.ui.TextInput(label="Quantidade", placeholder="Ex.: 20", min_length=1, max_length=3, default="20")

    def __init__(self, dj, category):
        super().__init__()
        self.dj = dj
        self.category = category

    async def on_submit(self, interaction):
        if not self.dj or not self.category:
            await interaction.response.send_message("Escolha um DJ e um tipo de estoque primeiro.", ephemeral=True)
            return
        try:
            quantity = int(str(self.quantity).strip())
            if not 1 <= quantity <= 100:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("A quantidade precisa ser de 1 a 100.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await SYSTEM.deliver(interaction, self.dj, self.category, quantity)


class SamplesChooseQuantityButton(discord.ui.Button):
    def __init__(self, parent):
        self.parent_view = parent
        super().__init__(label="Escolher quantidade", emoji="📦", style=discord.ButtonStyle.success, custom_id="ccbot:samples:quantity")

    async def callback(self, interaction):
        await interaction.response.send_modal(SamplesQuantityModal(self.parent_view.dj, self.parent_view.category))


class SamplesPurchaseView(discord.ui.LayoutView):
    def __init__(self, dj=None, category=None):
        super().__init__(timeout=300)
        self.dj = dj
        self.category = category
        self.step = "dj" if not dj else ("type" if not category else "quantity")
        self.refresh_items()

    def refresh_items(self):
        djs = SYSTEM.djs()
        rows = []
        if self.step == "dj":
            content = (
                "## 🎧 ESTILO DE SAMPLE\n"
                "**Qual DJ você quer que inspire suas samples?**\n\n"
                "Escolha um DJ na lista abaixo para continuar."
            )
            if djs:
                rows.append(discord.ui.ActionRow(SamplesDJSelect(self)))
            else:
                content += "\n\nNenhum DJ cadastrado ainda."
        elif self.step == "type":
            content = (
                "## 🎧 ESTILO DE SAMPLE\n"
                f"**DJ escolhido:** {discord.utils.escape_markdown(self.dj)}\n\n"
                "**Qual estilo de sample você quer?**\n"
                "Escolha uma opção. As opções de IA e de outros packs continuam inspiradas na temática desse DJ."
            )
            rows.append(discord.ui.ActionRow(SamplesTypeSelect(self)))
        else:
            content = (
                "## 📦 QUANTAS SAMPLES VOCÊ QUER?\n"
                f"**DJ:** {discord.utils.escape_markdown(self.dj)}\n"
                f"**Estilo:** {SYSTEM.stock_label(self.dj, self.category)}\n"
                f"_{SYSTEM.stock_summary(self.dj, self.category)}_\n\n"
                "Informe a quantidade para receber as samples por mensagem privada."
            )
            rows.append(discord.ui.ActionRow(SamplesChooseQuantityButton(self)))
        self.clear_items()
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(content=content), *rows))

    async def refresh(self, interaction):
        self.refresh_items()
        await interaction.response.edit_message(view=self)


class SamplesPurchaseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Escolher samples", emoji="🎧", style=discord.ButtonStyle.success, custom_id="ccbot:samples:purchase")

    async def callback(self, interaction):
        if not SYSTEM.djs():
            await interaction.response.send_message("Ainda não há DJs cadastrados.", ephemeral=True)
            return
        await interaction.response.send_message(view=SamplesPurchaseView(), ephemeral=True)


class SamplesPanelView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(content=(
                "## 🎧 GERADOR DE SAMPLES\n"
                "**Escolha uma opção por vez e monte seu lote de samples.**\n\n"
                "Primeiro escolha o DJ que vai inspirar as samples. Depois selecione o estilo e a quantidade. "
                "Você recebe os itens disponíveis por mensagem privada.\n\n"
                "-# O estoque é permanente. O bot registra o que cada pessoa já recebeu para evitar repetições."
            )),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
            discord.ui.ActionRow(SamplesPurchaseButton())
        ))


class SamplesAddModal(discord.ui.Modal, title="Adicionar ao estoque"):
    links = discord.ui.TextInput(
        label="Links (opcional)",
        placeholder="Cole um link por linha",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=4000
    )

    arquivos = discord.ui.Label(
        text="Arquivos soltos ou ZIPs (opcional)",
        description="Até 10 arquivos por envio. Todos os formatos são aceitos.",
        component=discord.ui.FileUpload(custom_id="ccbot_samples_upload", min_values=0, max_values=MAX_UPLOAD_FILES, required=False)
    )

    def __init__(self, dj, category):
        super().__init__()
        self.dj = dj
        self.category = category

    async def on_submit(self, interaction):
        if not SYSTEM.is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode gerenciar o estoque.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        added = 0
        try:
            attachments = list(self.arquivos.component.values or [])
            links = [line.strip() for line in str(self.links.value or "").splitlines() if line.strip()]
            if any(not url.startswith(("https://", "http://")) for url in links):
                raise ValueError("Cada link precisa começar com http:// ou https://.")
            if any(attachment.size > MAX_ITEM_BYTES for attachment in attachments):
                raise ValueError("Um ou mais arquivos ultrapassam 24 MB. Para arquivos grandes, adicione links.")
            for attachment in attachments:
                data = await attachment.read(use_cached=True)
                item_id = uuid.uuid4().hex
                name = _safe_name(attachment.filename)
                rel = f"{_slug(self.dj)}/{self.category}/{item_id}_{name}"
                dest = (SYSTEM.files_dir / rel).resolve()
                if SYSTEM.files_dir.resolve() not in dest.parents:
                    raise ValueError("Nome de arquivo inválido.")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                kind = "zip" if Path(name).suffix.lower() == ".zip" else "file"
                SYSTEM.add_item(self.dj, self.category, {"id": item_id, "kind": kind, "name": name, "path": rel})
                added += 1
            for url in links:
                SYSTEM.add_item(self.dj, self.category, {"id": uuid.uuid4().hex, "kind": "link", "name": url[:100], "url": url})
                added += 1
            await interaction.followup.send(f"Adicionados **{added}** item(ns) ao estoque de **{self.dj} / {SYSTEM.stock_label(self.dj, self.category)}**.", ephemeral=True)
        except (OSError, ValueError, discord.HTTPException) as exc:
            await interaction.followup.send(f"Não consegui concluir o envio: {str(exc)[:300]}", ephemeral=True)
        except Exception:
            log.exception("Falha ao adicionar itens ao estoque")
            await interaction.followup.send("Erro inesperado ao adicionar itens.", ephemeral=True)


class SamplesEditItemModal(discord.ui.Modal, title="Editar item do estoque"):
    nome = discord.ui.TextInput(label="Nome do item", max_length=180, required=True)
    link = discord.ui.TextInput(label="Link (somente para links)", max_length=1000, required=False)
    arquivo = discord.ui.Label(
        text="Substituir arquivo (opcional)",
        description="Anexe um arquivo para substituir o arquivo atual. ZIPs e outros formatos são aceitos.",
        component=discord.ui.FileUpload(custom_id="ccbot_samples_replace_file", min_values=0, max_values=1, required=False)
    )

    def __init__(self, dj, category, item):
        super().__init__()
        self.dj = dj
        self.category = category
        self.item_id = item["id"]
        self.nome.default = str(item.get("name", ""))[:180]
        self.link.default = str(item.get("url", ""))[:1000]

    async def on_submit(self, interaction):
        if not SYSTEM.is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode gerenciar o estoque.", ephemeral=True)
            return
        item = next((x for x in SYSTEM.items(self.dj, self.category) if x.get("id") == self.item_id), None)
        if item is None:
            await interaction.response.send_message("Esse item não está mais no estoque.", ephemeral=True)
            return
        url = str(self.link.value or "").strip()
        if item.get("kind") == "link" and not (url.startswith("https://") or url.startswith("http://")) and not list(self.arquivo.component.values or []):
            await interaction.response.send_message("Informe um link válido ou anexe o arquivo que substituirá este item.", ephemeral=True)
            return
        replacements = list(self.arquivo.component.values or [])
        old_item = dict(item)
        new_name = _safe_name(str(self.nome.value).strip())
        if replacements:
            attachment = replacements[0]
            if attachment.size > MAX_ITEM_BYTES:
                await interaction.response.send_message("O arquivo ultrapassa 24 MB. Para arquivos grandes, use um link.", ephemeral=True)
                return
            data = await attachment.read(use_cached=True)
            new_id = uuid.uuid4().hex
            name = _safe_name(attachment.filename)
            rel = f"{_slug(self.dj)}/{self.category}/{new_id}_{name}"
            dest = (SYSTEM.files_dir / rel).resolve()
            if SYSTEM.files_dir.resolve() not in dest.parents:
                await interaction.response.send_message("Nome de arquivo inválido.", ephemeral=True)
                return
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            item.update({
                "id": new_id,
                "kind": "zip" if Path(name).suffix.lower() == ".zip" else "file",
                "name": new_name or name,
                "path": rel
            })
            item.pop("url", None)
            SYSTEM.save_stock()
            SYSTEM.remove_item_file(old_item)
        elif item.get("kind") == "link":
            item["name"] = new_name
            item["url"] = url
            SYSTEM.save_stock()
        else:
            item["name"] = new_name
            SYSTEM.save_stock()
        await interaction.response.send_message("Item atualizado/substituído.", ephemeral=True)


class SamplesEditItemSelect(discord.ui.Select):
    def __init__(self, parent, action):
        self.parent_view = parent
        self.action = action
        items = SYSTEM.items(parent.dj, parent.category)[:25]
        options = [discord.SelectOption(label=f"{i+1}. {item.get('name', 'Item')}"[:100], value=item["id"]) for i, item in enumerate(items)]
        super().__init__(placeholder="Selecione um item...", min_values=1, max_values=1, options=options, custom_id="ccbot:samples:select_item")

    async def callback(self, interaction):
        if not SYSTEM.is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode gerenciar o estoque.", ephemeral=True)
            return
        item_id = self.values[0]
        items = SYSTEM.items(self.parent_view.dj, self.parent_view.category)
        item = next((x for x in items if x.get("id") == item_id), None)
        if item is None:
            await interaction.response.send_message("Esse item não está mais no estoque.", ephemeral=True)
            return
        if self.action == "edit":
            await interaction.response.send_modal(SamplesEditItemModal(self.parent_view.dj, self.parent_view.category, item))
            return
        items.remove(item)
        SYSTEM.save_stock()
        SYSTEM.remove_item_file(item)
        await interaction.response.edit_message(content="Item removido do estoque.", view=None)


class SamplesImportModal(discord.ui.Modal, title="Importar/substituir estoque"):
    arquivo = discord.ui.Label(
        text="Arquivo TXT ou ZIP exportado pelo CC BOT",
        description="Substitui o estoque atual. O histórico de entregas será mantido.",
        component=discord.ui.FileUpload(custom_id="ccbot_samples_import_file", min_values=1, max_values=1, required=True)
    )

    def __init__(self, dj, category):
        super().__init__()
        self.dj = dj
        self.category = category

    async def on_submit(self, interaction):
        if not SYSTEM.is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode importar estoque.", ephemeral=True)
            return
        files = list(self.arquivo.component.values or [])
        if not files:
            await interaction.response.send_message("Anexe um .txt ou .zip.", ephemeral=True)
            return
        await SYSTEM.import_replace(interaction, self.dj, self.category, files[0])


class SamplesClearConfirmView(discord.ui.View):
    def __init__(self, dj, category):
        super().__init__(timeout=120)
        self.dj = dj
        self.category = category

    @discord.ui.button(label="Confirmar apagar tudo", style=discord.ButtonStyle.danger, custom_id="ccbot:samples:clear_confirm")
    async def confirm(self, interaction, button):
        if not SYSTEM.is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode gerenciar o estoque.", ephemeral=True)
            return
        key = SYSTEM._key(self.dj, self.category)
        old_items = list(SYSTEM.stock.get(key, []))
        count = len(old_items)
        SYSTEM.stock[key] = []
        SYSTEM.save_stock()
        for item in old_items:
            SYSTEM.remove_item_file(item)
        await interaction.response.edit_message(content=f"Estoque apagado: {count} item(ns). O histórico individual foi preservado.", view=None)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, custom_id="ccbot:samples:clear_cancel")
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Operação cancelada.", view=None)


class SamplesAdminActionButton(discord.ui.Button):
    def __init__(self, parent, action, label, emoji, style):
        self.parent_view = parent
        self.action = action
        super().__init__(label=label, emoji=emoji, style=style, custom_id=f"ccbot:samples:admin:{action}")

    async def callback(self, interaction):
        await self.parent_view.handle_action(interaction, self.action)


class SamplesPublishButton(discord.ui.Button):
    def __init__(self, parent):
        self.parent_view = parent
        super().__init__(label="Publicar painel", emoji="🚀", style=discord.ButtonStyle.success, custom_id="ccbot:samples:publish")

    async def callback(self, interaction):
        if not SYSTEM.is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode publicar o painel.", ephemeral=True)
            return
        if not self.parent_view.publish_callback:
            await interaction.response.send_message("A publicação não está configurada nesta sessão.", ephemeral=True)
            return
        await self.parent_view.publish_callback(interaction)


class SamplesStockNameModal(discord.ui.Modal):
    name = discord.ui.TextInput(label="Nome do stock", placeholder="Ex.: Samples por IA", min_length=1, max_length=90)

    def __init__(self, parent, rename=False):
        super().__init__(title="Renomear stock" if rename else "Criar stock")
        self.parent_view = parent
        self.rename = rename
        if rename and parent.dj and parent.category:
            self.name.default = SYSTEM.stock_label(parent.dj, parent.category)[:90]

    async def on_submit(self, interaction):
        if not SYSTEM.is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode gerenciar o estoque.", ephemeral=True)
            return
        try:
            if self.rename:
                SYSTEM.rename_stock_type(self.parent_view.dj, self.parent_view.category, self.name.value)
            else:
                self.parent_view.category = SYSTEM.add_stock_type(self.parent_view.dj, self.name.value)
            self.parent_view.refresh_items()
            await interaction.response.send_message(
                ("Stock renomeado para **" if self.rename else "Stock criado: **")
                + discord.utils.escape_markdown(self.name.value.strip()) + "**. Reabra o painel de estoque para atualizar a lista.",
                ephemeral=True
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)


class SamplesAdminView(discord.ui.LayoutView):
    def __init__(self, dj=None, category=None, publish_callback=None):
        super().__init__(timeout=300)
        self.dj = dj or (SYSTEM.djs()[0] if SYSTEM.djs() else None)
        categories = SYSTEM.stock_categories(self.dj) if self.dj else []
        self.category = category if any(x["id"] == category for x in categories) else (categories[0]["id"] if categories else None)
        self.publish_callback = publish_callback
        self.refresh_items()

    def refresh_items(self):
        djs = SYSTEM.djs()
        categories = SYSTEM.stock_categories(self.dj) if self.dj else []
        if self.category and not any(x["id"] == self.category for x in categories):
            self.category = categories[0]["id"] if categories else None
        items = SYSTEM.items(self.dj, self.category) if self.dj and self.category else []
        label = SYSTEM.stock_label(self.dj, self.category) if self.dj and self.category else "Nenhum criado"
        text = (
            "## 🗃️ ADMINISTRAÇÃO DO ESTOQUE DE SAMPLES\n"
            "Cada DJ pode ter seus próprios stocks e nomes. Crie ou renomeie cada um separadamente.\n\n"
            f"**DJ:** {discord.utils.escape_markdown(self.dj or 'Nenhum cadastrado')}\n"
            f"**Stock:** {discord.utils.escape_markdown(label)}\n"
            f"**Itens:** {len(items)}\n\n"
            "Adicione arquivos, ZIPs ou links ao stock selecionado.\n\n"
            "A pessoa verá somente os stocks cadastrados para o DJ escolhido."
        )
        self.clear_items()
        rows = []
        if djs:
            rows.append(discord.ui.ActionRow(SamplesDJSelect(self)))
        if categories:
            rows.append(discord.ui.ActionRow(SamplesTypeSelect(self)))
        rows.append(discord.ui.ActionRow(
            SamplesAdminActionButton(self, "create_stock", "Criar stock", "🆕", discord.ButtonStyle.success),
            SamplesAdminActionButton(self, "rename_stock", "Renomear stock", "✏️", discord.ButtonStyle.secondary)
        ))
        buttons = [
            SamplesAdminActionButton(self, "add", "Adicionar", "➕", discord.ButtonStyle.success),
            SamplesAdminActionButton(self, "edit", "Editar item", "✏️", discord.ButtonStyle.secondary),
            SamplesAdminActionButton(self, "delete", "Apagar item", "🗑️", discord.ButtonStyle.secondary),
            SamplesAdminActionButton(self, "clear", "Apagar estoque", "⚠️", discord.ButtonStyle.danger),
            SamplesAdminActionButton(self, "txt", "Exportar TXT", "📄", discord.ButtonStyle.primary),
            SamplesAdminActionButton(self, "zip", "Exportar ZIP", "📦", discord.ButtonStyle.primary),
            SamplesAdminActionButton(self, "import", "Importar/substituir", "🔄", discord.ButtonStyle.primary),
            SamplesAdminActionButton(self, "history", "Histórico", "📜", discord.ButtonStyle.secondary),
        ]
        rows.append(discord.ui.ActionRow(*buttons[:4]))
        rows.append(discord.ui.ActionRow(*buttons[4:8]))
        if self.publish_callback:
            rows.append(discord.ui.ActionRow(SamplesPublishButton(self)))
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(content=text), *rows))

    async def handle_action(self, interaction, action):
        if not SYSTEM.is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode gerenciar o estoque.", ephemeral=True)
            return
        if not self.dj:
            await interaction.response.send_message("Cadastre um DJ primeiro.", ephemeral=True)
            return
        if action == "create_stock":
            await interaction.response.send_modal(SamplesStockNameModal(self, rename=False))
        elif action == "rename_stock":
            if not self.category:
                await interaction.response.send_message("Crie um stock primeiro.", ephemeral=True)
            else:
                await interaction.response.send_modal(SamplesStockNameModal(self, rename=True))
        elif action == "add":
            if not self.category:
                await interaction.response.send_message("Crie um stock para esse DJ primeiro.", ephemeral=True)
                return
            await interaction.response.send_modal(SamplesAddModal(self.dj, self.category))
        elif action in ("delete", "edit"):
            items = SYSTEM.items(self.dj, self.category)
            if not items:
                await interaction.response.send_message("Esse estoque está vazio.", ephemeral=True)
            else:
                await interaction.response.send_message(
                    "Escolha o item que deseja " + ("editar:" if action == "edit" else "remover:"),
                    view=SamplesEditItemSelectView(self.dj, self.category, action),
                    ephemeral=True
                )
        elif action == "clear":
            if not self.category:
                await interaction.response.send_message("Crie um stock primeiro.", ephemeral=True)
                return
            await interaction.response.send_message(f"Confirma apagar os **{len(SYSTEM.items(self.dj, self.category))}** itens de **{self.dj} / {SYSTEM.stock_label(self.dj, self.category)}**?", view=SamplesClearConfirmView(self.dj, self.category), ephemeral=True)
        elif action == "import":
            if not self.category:
                await interaction.response.send_message("Crie um stock primeiro.", ephemeral=True)
                return
            await interaction.response.send_modal(SamplesImportModal(self.dj, self.category))
        elif action == "history":
            records = []
            prefix = "::" + self.dj + "::" + self.category
            for key, sent in SYSTEM.history.items():
                if key.endswith(prefix):
                    user_id = key.split("::", 1)[0]
                    records.append((user_id, len(sent) if isinstance(sent, list) else 0))
            records.sort(key=lambda item: item[1], reverse=True)
            if not records:
                await interaction.response.send_message("Ainda não há entregas registradas para este DJ e tipo de estoque.", ephemeral=True)
                return
            lines = [f"• <@{user_id}> (ID {user_id}): **{count}** item(ns)" for user_id, count in records[:40]]
            content = (
                f"## 📜 HISTÓRICO INDIVIDUAL\n**{discord.utils.escape_markdown(self.dj)}** · "
                f"**{SYSTEM.stock_label(self.dj, self.category)}**\n\n" + "\n".join(lines)
            )
            if len(records) > 40:
                content += f"\n\nMostrando 40 de {len(records)} usuários."
            await interaction.response.send_message(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            if not self.category:
                await interaction.response.send_message("Crie um stock primeiro.", ephemeral=True)
                return
            await SYSTEM.admin_export(interaction, self.dj, self.category, action)

    async def refresh(self, interaction):
        self.refresh_items()
        await interaction.response.edit_message(view=self)


class SamplesEditItemSelectView(discord.ui.View):
    def __init__(self, dj, category, action):
        super().__init__(timeout=300)
        items = SYSTEM.items(dj, category)[:25]
        if items:
            self.add_item(SamplesEditItemSelect(type("Parent", (), {"dj": dj, "category": category})(), action))


class SamplesAdminButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Gerenciar estoque", emoji="🗃️", style=discord.ButtonStyle.primary, custom_id="ccbot:samples:manage")

    async def callback(self, interaction):
        if not SYSTEM.is_admin(interaction):
            await interaction.response.send_message("Apenas o administrador pode gerenciar o estoque.", ephemeral=True)
            return
        await interaction.response.send_message(view=SamplesAdminView(), ephemeral=True)


def samples_admin_view():
    return SamplesAdminView()


def samples_panel_view():
    return SamplesPanelView()
