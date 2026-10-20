"""Ponte segura entre o CC BOT em execução e tarefas registradas no GitHub.

Apenas ações de leitura previamente permitidas são aceitas. Nunca executa código
ou comandos recebidos do repositório. O token GitHub fica fora do repositório.
"""
import asyncio
import base64
import json
import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = "youngbae902-max/ccbot"
BRANCH = "master"
INBOX_PATH = "tasks/inbox.json"
GUILD_ID = 1561513043886743562
GITHUB_TOKEN_FILE = Path("/sdcard/Download/CC/github_token.txt")
STATE_FILE = Path.home() / "ccbot-repo" / "config" / "github_bridge_state.json"
POLL_SECONDS = 30
MAX_TASKS_PER_POLL = 5
MAX_RESPONSE_BYTES = 2_000_000

log = logging.getLogger("CCBOT.GITHUB")
ALLOWED_ACTIONS = {"diagnostico", "saude"}

CHANNEL_CHECKS = [
    ("Pedidos", 1561513284191125625, ("view_channel", "send_messages", "embed_links", "attach_files")),
    ("Boas-vindas", 1561513269754068992, ("view_channel", "send_messages", "embed_links")),
    ("Origem do contador", 1561513284191125625, ("view_channel", "read_message_history")),
    ("Painel Samples", 1561513284811624480, ("view_channel", "read_message_history", "send_messages")),
    ("Painel Pack", 1561513286967500841, ("view_channel", "read_message_history", "send_messages")),
    ("Painel Acapellas", 1561794460617277511, ("view_channel", "send_messages", "embed_links")),
]

def _token():
    try:
        token = GITHUB_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return token

def _api_request(method, path, token, payload=None):
    url = "https://api.github.com" + path
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "CCBOT-GitHub-Bridge",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("Resposta do GitHub excede o limite de segurança.")
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API retornou HTTP {exc.code}: {detail}") from exc

def _read_inbox(token):
    path = f"/repos/{REPOSITORY}/contents/{INBOX_PATH}?ref={BRANCH}"
    data = _api_request("GET", path, token)
    content = data.get("content", "")
    decoded = base64.b64decode(content).decode("utf-8")
    inbox = json.loads(decoded)
    if not isinstance(inbox, dict) or not isinstance(inbox.get("requests"), list):
        raise ValueError("tasks/inbox.json deve conter um objeto com uma lista 'requests'.")
    return inbox["requests"]

def _load_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(state, dict) and isinstance(state.get("processed"), list):
            return state
    except (OSError, json.JSONDecodeError):
        pass
    return {"processed": []}

def _save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)

def _upload_report(token, request_id, report):
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", request_id)[:80]
    if not safe_id:
        raise ValueError("ID de solicitação inválido.")
    path = f"/repos/{REPOSITORY}/contents/reports/{safe_id}.md"
    payload = {
        "message": f"Relatório automático CC BOT: {safe_id}",
        "content": base64.b64encode(report.encode("utf-8")).decode("ascii"),
        "branch": BRANCH,
    }
    _api_request("PUT", path, token, payload)

async def _diagnostico(guild):
    vazios, com_mensagens, sem_acesso = [], 0, []
    total = len(guild.text_channels)
    for canal in guild.text_channels:
        try:
            encontrou = False
            async for _ in canal.history(limit=1, oldest_first=False):
                encontrou = True
                break
            if encontrou:
                com_mensagens += 1
            else:
                categoria = canal.category.name if canal.category else "Sem categoria"
                vazios.append(f"- #{canal.name} — categoria: {categoria} — ID: {canal.id}")
        except Exception as exc:
            categoria = canal.category.name if canal.category else "Sem categoria"
            sem_acesso.append(
                f"- #{canal.name} — categoria: {categoria} — ID: {canal.id} — {type(exc).__name__}"
            )
            log.warning("Ponte GitHub: não foi possível ler o canal %s: %s", canal.id, type(exc).__name__)

    return "\n".join([
        f"# Diagnóstico do servidor — {guild.name}",
        f"- Servidor ID: {guild.id}",
        f"- Data: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- Canais de texto analisados: {total}",
        f"- Com mensagens visíveis: {com_mensagens}",
        f"- Sem mensagens visíveis: {len(vazios)}",
        f"- Não verificáveis: {len(sem_acesso)}",
        "",
        "## Canais sem mensagens",
        *(vazios or ["Nenhum canal vazio encontrado."]),
        "",
        "## Canais não verificáveis",
        *(sem_acesso or ["Todos os históricos puderam ser consultados."]),
        "",
        "> Auditoria somente de leitura: nenhum canal, nome, permissão ou mensagem foi alterado.",
    ])

async def _saude(bot, guild):
    lines = [
        f"# Saúde do CC BOT — {guild.name}",
        f"- Servidor ID: {guild.id}",
        f"- Bot conectado: {'SIM' if bot.is_ready() else 'NÃO'}",
        f"- Latência: {round(bot.latency * 1000)} ms" if bot.is_ready() else "- Latência indisponível",
        f"- Intent members: {'ativo' if bot.intents.members else 'inativo'}",
        f"- Intent message_content: {'ativo' if bot.intents.message_content else 'inativo'}",
        "",
        "## Verificação de permissões",
    ]
    falhas = 0
    membro_bot = guild.me
    for nome, canal_id, requeridas in CHANNEL_CHECKS:
        try:
            canal = bot.get_channel(canal_id) or await bot.fetch_channel(canal_id)
            if getattr(getattr(canal, "guild", None), "id", None) != guild.id:
                lines.append(f"- FALHA — {nome}: canal fora deste servidor (ID {canal_id})")
                falhas += 1
                continue
            perms = canal.permissions_for(membro_bot)
            faltando = [p for p in requeridas if not getattr(perms, p, False)]
            if faltando:
                lines.append(f"- ATENÇÃO — {nome} ({canal_id}): faltam {', '.join(faltando)}")
                falhas += 1
            else:
                lines.append(f"- OK — {nome} ({canal_id})")
        except Exception as exc:
            lines.append(f"- FALHA — {nome} ({canal_id}): {type(exc).__name__}")
            falhas += 1
    lines.extend(["", f"Alertas/falhas: {falhas}", "> Verificação somente de leitura; nenhuma configuração foi alterada."])
    return "\n".join(lines)

async def process_request(bot, request, token):
    if not isinstance(request, dict):
        raise ValueError("Solicitação deve ser um objeto JSON.")
    request_id = request.get("id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id):
        raise ValueError("A solicitação precisa de um ID simples (letras, números, _ ou -).")
    action = request.get("action")
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Ação não permitida: {action!r}. Permitidas: {', '.join(sorted(ALLOWED_ACTIONS))}.")
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        raise RuntimeError(f"Servidor Discord {GUILD_ID} não está disponível para o bot.")
    if action == "diagnostico":
        body = await _diagnostico(guild)
    else:
        body = await _saude(bot, guild)
    report = "\n".join([
        f"<!-- request_id: {request_id} -->",
        f"<!-- action: {action} -->",
        body,
        "",
        "## Controle da solicitação",
        f"- ID: {request_id}",
        f"- Ação: {action}",
        f"- Finalizado (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "- Origem: ponte GitHub do CC BOT",
    ])
    await asyncio.to_thread(_upload_report, token, request_id, report)
    return request_id

async def run(bot):
    """Sonda a caixa de entrada a cada 30 s. Ausência de token desativa a ponte."""
    token = _token()
    if not token:
        log.warning(
            "Ponte GitHub desativada: crie %s com um token Fine-grained que tenha Contents read/write apenas neste repositório.",
            GITHUB_TOKEN_FILE,
        )
        return
    log.info("Ponte GitHub iniciada; verificações a cada %s segundos.", POLL_SECONDS)
    while not bot.is_closed():
        try:
            requests = await asyncio.to_thread(_read_inbox, token)
            state = _load_state()
            processed = set(state["processed"])
            pending = [r for r in requests if isinstance(r, dict) and r.get("id") not in processed]
            for request in pending[:MAX_TASKS_PER_POLL]:
                request_id = request.get("id", "invalid")
                try:
                    completed_id = await process_request(bot, request, token)
                    processed.add(completed_id)
                    state["processed"] = sorted(processed)
                    _save_state(state)
                    log.info("Solicitação GitHub concluída: %s", completed_id)
                except Exception:
                    log.exception("Falha ao processar solicitação GitHub %s", request_id)
            if len(pending) > MAX_TASKS_PER_POLL:
                log.warning("Fila GitHub tem %s solicitações pendentes; limite por ciclo=%s", len(pending), MAX_TASKS_PER_POLL)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ponte GitHub: falha ao consultar a fila; nova tentativa em %s s.", POLL_SECONDS)
        await asyncio.sleep(POLL_SECONDS)
