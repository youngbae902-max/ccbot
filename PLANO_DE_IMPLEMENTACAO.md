# Plano de implementação do CC BOT

Este documento acompanha as melhorias do repositório. Os recursos existentes devem ser preservados; mudanças que apagam, renomeiam ou publicam conteúdo no servidor precisam de confirmação explícita.

## Fase 1 — Base segura e diagnóstico

- [x] Documentar instalação e dependências.
- [x] Executar verificação de sintaxe e testes estáticos no GitHub Actions.
- [x] Criar `/diagnostico` para identificar canais de texto vazios ou cujo histórico não pode ser lido.
- [x] Manter o diagnóstico somente de leitura.
- [x] Direcionar pedidos de beat, pack e samples ao canal central `1561513284191125625`.

## Fase 2 — Criação guiada

- [x] Fazer uma pergunta por vez: tipo, título, objetivo, texto e estilo.
- [x] Mostrar uma prévia e exigir confirmação antes de publicar.
- [ ] Adicionar modelos de Componentes V2 depois de definir as ações reais de cada painel.
- [ ] Adicionar edição de uma mensagem existente como padrão para painéis já publicados.

## Fase 3 — Auditoria do bot e confiabilidade

- [x] Criar `/saude` para verificar permissões básicas dos canais configurados e intents.
- [ ] Verificar interações sem resposta, erros de API e tarefas em duplicidade.
- [ ] Testar reinício e reconexão sem duplicar views, contadores ou mensagens.
- [ ] Revisar permissões por comando e validar IDs/configurações sem alterar o servidor automaticamente.

## Fase 4 — Fluxos comerciais

- [ ] Revisar o Gerador de Samples: quantidade, seleção de DJ, histórico por comprador e prevenção de repetição.
- [ ] Separar claramente pedido recebido, pagamento confirmado, processamento e entrega.
- [ ] Pack Personalizado: perguntas claras, cálculo de adicionais, recibo, status e prazo.
- [ ] Beats: prévia, arquivo/ZIP, registro de pedido e entrega.
- [ ] Preservar o canal central de pedidos e nunca mandar pedidos de compra ao PV do administrador.
- [ ] Não tratar pagamentos como reais enquanto não houver gateway configurado e testado.

## Fase 5 — Arrumador de projetos

- [ ] Validar ZIPs com segurança, limitar tamanho e armazenar pedidos com status.
- [ ] Criar análise real de arquivos do projeto e relatório do que foi possível identificar.
- [ ] Só prometer alterações automáticas depois de testar formatos reais de projetos do FL Studio Mobile.
- [ ] Definir fluxo de entrega, prazo e suporte; qualquer IA externa exigirá configuração segura de credenciais.

## Fase 6 — Comunidade e conteúdo

- [ ] Preparar triagem de contribuições enviadas pela comunidade.
- [ ] Pesquisar packs/projetos gratuitos e verificar licença/origem antes de sugerir publicação.
- [ ] Exigir revisão humana antes de publicar conteúdo encontrado na internet.
- [ ] Revisar os canais de divulgação, suporte e parcerias sem renomear canais automaticamente.

## Limites técnicos atuais

