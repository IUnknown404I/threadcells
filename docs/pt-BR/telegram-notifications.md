---
slug: telegram-notifications
source: docs/TELEGRAM_NOTIFICATIONS.md
source_sha256: sha256:c1c50ae5d9e7937dff2794e49e2914929e7d02e4adb3de0c2f04c7cd5d656735
---

# Notificações do Telegram

O ThreadCells pode enviar notificações discretas de ciclo de vida para um destino do Telegram. Esta é uma capacidade global da instalação do ThreadCells: ela não pertence ao projeto selecionado no momento, não lê sua configuração dele nem depende dele.

![Configurações de notificação do Telegram ao vivo com os campos de destino e credencial explicitamente ocultados](/media/screenshots/threadcells-telegram.webp)

## Configurar o destino

1. Crie ou escolha um bot do Telegram usando o fluxo compatível de gerenciamento de bots do Telegram.
2. Obtenha o ID do chat de destino. Para um tópico de fórum, obtenha também seu ID positivo de thread de mensagem.
3. Abra **Settings → Telegram** e desbloqueie as alterações de operador.
4. Informe o token do bot, o ID do chat e o ID opcional do tópico/thread.
5. Salve enquanto as notificações estão desativadas.
6. Use **Check connection** para validar a credencial do bot e, depois, **Send test notification** para validar o destino.
7. Ative as notificações e salve novamente.

A ação de teste é explícita; abrir Settings nunca contata o Telegram. Desativar notificações conserva o destino e o token configurados para que possam ser reativados depois. **Clear bot token** é uma ação de operador separada e confirmada: ela remove a credencial, desativa notificações e conserva os campos de destino não secretos.

## Tratamento de segredos

A Web UI envia um novo token somente em uma atualização protegida e limpa seu campo de senha em seguida. As APIs de leitura informam somente `Configured`, `Not configured` ou `Invalid`; elas nunca retornam o token. O ThreadCells não coloca o token no armazenamento do navegador, em prompts de terminal, metadados de sessão ou agente, logs normais nem na linha de configurações do SQLite.

O servidor armazena o token em:

```text
$CAO_HOME_DIR/secrets/telegram-bot-token
```

O diretório pai é restrito à conta de runtime e o arquivo do token usa o modo `0600`. A substituição usa uma renomeação atômica do sistema de arquivos; a limpeza desvincula a credencial sem segui-la e sincroniza o diretório de segredos. `CAO_HOME_DIR` é a raiz de estado mutável privada da instalação, não um caminho público do repositório.

Trate esse arquivo como credencial. Não o copie para controle de versão, bundles comuns de suporte, exportações de banco de dados, histórico de shell ou capturas de tela. Faça a rotação pelo Telegram se suspeitar de divulgação.

## Política de notificações

A política da primeira versão envia no máximo uma tentativa para cada evento durável de fluxo de trabalho de nível superior:

- conclusão bem-sucedida de nível superior;
- um bloqueio de atenção do proprietário de nível superior;
- falha inesperada de um terminal de nível superior enquanto seu fluxo de trabalho está aberto.

O ThreadCells não notifica sobre conclusão de filho, delegação, polling, atualizações de progresso, ciclos internos de repetição nem todo turno de modelo/ferramenta. Chaves de eventos duráveis evitam que uma observação repetida ou reinicialização duplique uma entrega já reivindicada.

As mensagens contêm somente contexto seguro e conciso: identidade do ThreadCells, sessão, nome de exibição do projeto quando presente, estado do ciclo de vida, resumo fixo e marca de tempo UTC. Elas não incluem prompts, saída do modelo, dumps do sistema de arquivos, corpos de exceção, segredos do operador nem o token do bot.

## Comportamento em falhas

A entrega ao Telegram falha de modo aberto para o trabalho dos agentes. Um timeout, uma credencial rejeitada ou serviço do Telegram indisponível registra um código de resultado seguro, mas não pode falhar nem reabrir o fluxo de trabalho. A entrega tem uma única tentativa delimitada; o ThreadCells não tenta novamente sem fim nem repete eventos históricos depois que as notificações são ativadas.

**Check connection** valida o token do bot com o Telegram. **Send test notification** também valida o roteamento configurado de chat/tópico. Uma verificação de conexão bem-sucedida não prova que o bot consegue escrever no destino escolhido, portanto use ambas as ações ao configurar um novo destino.

## Backup e restauração

O estado não secreto de ativação/destino e o registro de entregas estão no banco de dados do ThreadCells. O token do bot é separado. Se as notificações precisarem sobreviver à recuperação de desastre, faça backup do token como credencial separada e criptografada, com propriedade e modo preservados; não o adicione a um arquivo rotineiro de banco de dados em texto simples.

Após restaurar, verifique o caminho e as permissões do segredo, mantenha inicialmente as notificações desativadas, execute ambas as verificações explícitas e então habilite a entrega. Restaurar o banco de dados sem o token informa com segurança `Not configured`.

## Solução de problemas

- **Não configurado:** forneça um token de bot válido e o ID do chat antes de ativar.
- **Armazenamento de token inválido:** verifique se o token é um arquivo regular, não symlink, pertencente à conta de runtime e sem permissões de grupo/outros.
- **Falha de conexão:** verifique HTTPS/DNS de saída e faça rotação ou substitua um token de bot rejeitado; erros seguros da UI omitem deliberadamente detalhes da resposta do Telegram.
- **A conexão funciona, mas o teste falha:** confirme que o bot pertence ao destino e pode publicar nele; verifique os IDs do chat e do tópico opcional.
- **Não há mensagem de ciclo de vida:** confirme que Enabled está ligado e lembre-se de que somente conclusão de nível superior, atenção do proprietário e falha inesperada de nível superior notificam. Eventos ocorridos enquanto estava desativado não são repetidos.
