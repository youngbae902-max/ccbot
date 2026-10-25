# CC BOT

Bot unificado do servidor C.C, feito com Python e `discord.py`.

## Requisitos

- Python 3.10 ou superior.
- `discord.py` 2.7.1 ou compatível dentro da série 2.x.
- Token do bot guardado fora do Git.
- Permissões do Discord necessárias para os recursos habilitados.

## Termux

No clone do projeto:

```bash
pkg update
pkg install python git
python -m pip install -r requirements.txt
```

O código espera o token em `/sdcard/Download/CC/token.txt`. Crie esse arquivo localmente e mantenha-o fora do repositório. Nunca publique o token em commits, mensagens ou logs.

O arquivo versionado `config/config.json` é apenas um modelo. Na primeira inicialização desta versão, as configurações existentes são migradas para `config/runtime_settings.json`, que é ignorado pelo Git; o arquivo versionado volta ao modelo padrão para evitar que vendas e ajustes locais bloqueiem `git pull`. Arquivos JSON inválidos são preservados em um backup antes de serem recriados.

Para iniciar manualmente:

```bash
python ccbot.py
```

## Comandos administrativos

- `/painel`: abre a central administrativa.
- `/status`: verifica a conexão.
- A ponte GitHub recebe tarefas em `tasks/inbox.json` e grava relatórios em `reports/`, sem envio manual de TXT.
- As ações disponíveis inicialmente são `diagnostico` e `saude`; ambas são somente de leitura.

### Configurar a ponte GitHub no Termux

1. Crie um token Fine-grained do GitHub com acesso **Contents: Read and write** somente ao repositório `youngbae902-max/ccbot`. Não use nem publique o token do Discord.
2. Salve o token, sem espaços extras, em `/sdcard/Download/CC/github_token.txt`.
3. Atualize o clone do repositório e reinicie o CC BOT. Se a ponte não estiver configurada, o bot continua funcionando normalmente e registra um aviso no log.
4. Para solicitar uma análise, adicione um objeto à lista `requests` em `tasks/inbox.json`, com um ID único e uma ação permitida. A ponte consulta a fila a cada 30 segundos e publica o resultado em `reports/ID.md`.

Exemplo de solicitação: `{"requests":[{"id":"diagnostico-001","action":"diagnostico"}]}`.

O estado de solicitações já processadas fica localmente em `config/github_bridge_state.json`, fora da fila versionada, para evitar executar o mesmo ID de novo. Não reutilize IDs. A ponte não executa código, comandos de shell nem instruções arbitrárias vindas do GitHub. As ações atuais são somente de leitura; alterações reais nos canais exigem uma ação específica implementada e uma confirmação explícita antes de ser adicionada à fila.

## Testes

```bash
python -m compileall -q .
python -m unittest discover -s tests -v
```

Os testes automatizados verificam sintaxe e contratos estáticos. Eles não substituem testes reais no Discord, onde permissões, intents, interações e limites da API precisam ser confirmados.
