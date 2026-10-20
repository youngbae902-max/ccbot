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

Para iniciar manualmente:

```bash
python ccbot.py
```

## Comandos administrativos

- `/painel`: abre a central administrativa.
- `/status`: verifica a conexão.
- `/diagnostico`: gera um relatório somente de leitura dos canais de texto, identificando canais sem mensagens e canais cujo histórico não pôde ser consultado.

O diagnóstico não renomeia, não apaga e não publica mensagens. Canais sem mensagens devem ser revisados por uma pessoa antes de qualquer alteração.

## Testes

```bash
python -m py_compile ccbot.py
python -m unittest discover -s tests -v
```

Os testes automatizados verificam sintaxe e contratos estáticos. Eles não substituem testes reais no Discord, onde permissões, intents, interações e limites da API precisam ser confirmados.
