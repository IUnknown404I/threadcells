---
slug: troubleshooting
source: docs/TROUBLESHOOTING.md
source_sha256: sha256:5e66928bd64c8837b5480d71160eb1548dbe9369be3069800fcf18e0f1e99836
---
# Solução de problemas

Comece preservando evidências: identidade de build atual, texto de erro seguro, sessão/workflow afetado, status de capacidade, logs recentes e status do Git. Evite limpeza, exclusão ou novas tentativas às cegas até saber se uma operação durável já foi concluída.

## A Web UI não inicia

**Verificações:** execute o servidor em primeiro plano, chame `curl -fsS http://127.0.0.1:9889/health`, confirme que a porta está escutando no loopback e inspecione Settings → About quando disponível.

**Resolução:** corrija o erro de dependência/configuração informado ou o conflito de porta. Se a verificação de integridade funcionar mas os arquivos estáticos não, verifique o candidato e assegure que o código Python e os ativos Web empacotados venham do mesmo build.

## O navegador em outra máquina não consegue se conectar

Isso é esperado quando o ThreadCells escuta corretamente no loopback. Não o altere para uma associação pública. Use o túnel SSH ou proxy autenticado em [Acesso remoto](REMOTE_ACCESS.md).

## O provedor mostra que a CLI não está instalada

**Verificações:** compare Settings → Providers com Spawn Agent, então execute `command -v PROVIDER_COMMAND` como o usuário de runtime do ThreadCells.

**Resolução:** instale a CLI canônica do provedor para essa conta, corrija seu `PATH` de serviço ou escolha outro provedor pronto. O registro do adaptador por si só não é instalação.

## O provedor está instalado, mas não autenticado

**Verificações:** execute o comando de status de autenticação suportado pelo provedor como o usuário de runtime.

**Resolução:** conclua o fluxo de login nativo do provedor. O ThreadCells não copia as credenciais de outro usuário nem faz login durante o preflight.

## O provedor informa que a prontidão não foi verificada

O comando existe, mas não consegue expor uma informação segura e não interativa sobre a autenticação. Verifique sua versão e execute um pequeno teste nativo. Ele pode continuar inicializável; inspecione o terminal resultante em busca de um prompt de login do provedor.

## O agente não inicia

**Verificações:** prontidão do provedor, prévia resolvida do perfil selecionado, caminho/permissões do projeto, capacidade de residente/Provedor/Work, disponibilidade do tmux e saída de inicialização do terminal.

**Resolução:** corrija a primeira admissão com falha ou o pré-requisito do provedor. Não inicie duplicatas repetidamente enquanto uma primeira sessão ainda estiver iniciando.

## Capacidade esgotada

Abra Orchestration Capacity e identifique a categoria exata que está cheia. Desative trabalhos concluídos com segurança ou aguarde a tarefa correspondente do provedor/pesada. Aumente somente esse limite quando o host e a cota tiverem margem medida.

## Slot de execução pesada indisponível

Um build, teste de navegador, varredura ou trabalho de recuperação detém o slot Heavy. Aguarde por ele ou investigue um lease obsoleto por meio do status canônico. Não execute um comando caro fora da admissão apenas para contornar a fila.

## Workflow aguardando o proprietário

Leia o motivo do gate. Forneça a decisão solicitada somente se ela for um limite genuíno de publicação, confiança, ação destrutiva, custo ou semântica de produto. Um final comum do provedor deve deixar o trabalho autônomo elegível aberto; informe um encerramento automático como defeito de workflow.

## Resultado não incorporado

Confirme que o filho registrou um resultado durável e que ele foi entregue ao pai correto. O pai deve ler/usar o resultado imutável e então reconhecer a incorporação. A repetição após reinicialização pode entregar novamente um resultado não reconhecido; não o aplique duas vezes.

## Nova entrada do proprietário fica na fila atrás de um workflow fechado

Reinicie o runtime suportado uma vez e inspecione as identidades exatas de workflow e Inbox. Os builds atuais reconciliam um transporte comum de Inbox pendente cujo workflow associado não está mais aberto e, então, permitem que o turno mais recente do proprietário aberto continue. Não reassocie nem edite manualmente a linha da Inbox; retenha o banco de dados e informe um defeito se o transporte obsoleto permanecer pendente ou qualquer carga cruzar a identidade do workflow.

## Autorização do operador não configurada

Confirme que `THREADCELLS_OPERATOR_VERIFIER_FILE` alcança o processo real do servidor e reinicie. Se a configuração for inválida, verifique o esquema, o caminho absoluto/canônico, proprietário/modo do arquivo, legibilidade e cada diretório pai. A conta de serviço não deve possuir nem conseguir substituir o verificador.

## O segredo correto do operador falha

Confirme que o servidor carregou o mesmo verificador que a CLI gerou. O mínimo é exatamente cinco caracteres. Verifique se há um processo de servidor antigo ou um verificador substituído recentemente; não registre o segredo informado.

## O Telegram não está configurado ou um teste falha

Abra Settings → Telegram após desbloquear as alterações do operador. `Not configured` requer um token de bot válido e o ID do chat. `Invalid` significa que o arquivo de token privado falhou nas verificações de proprietário, arquivo regular ou modo. Uma verificação de conexão bem-sucedida valida a credencial do bot; envie uma notificação de teste explícita para validar o chat e o ID opcional de tópico. Verifique HTTPS/DNS de saída se qualquer ação falhar. Erros seguros omitem intencionalmente os corpos de resposta do Telegram e o token. Veja [Notificações do Telegram](TELEGRAM_NOTIFICATIONS.md).

## Statistics não mostra uma sessão atual

Atualize uso/status, verifique se o provedor oferece suporte a telemetria e confirme que sua evidência de rollout durável permanece legível. As sessões não precisam ser excluídas antes da contagem. Campos ausentes do provedor devem informar Not reported, não zero.

## O total de Statistics parece duplicado

Compare as dimensões global, de sessão e de terminal e preserve o banco de dados. Snapshots cumulativos do provedor devem atualizar um checkpoint estável entre polling/reinicialização/reprodução. Não exclua linhas manualmente antes do diagnóstico.

## Incompatibilidade entre Docs e identidade de build

Settings → About, rodapé de Docs, manifesto do candidato e revisão dos ativos estáticos devem concordar. Reconstrua e verifique um candidato imutável; não combine a saída Web de um checkout com código Python de outro.

## Pressão de disco ou Housekeeping não consegue recuperar espaço

Inspecione um plano de execução simulada do Housekeeping. Itens protegidos, ativos, desconhecidos, de backup, atuais e de rollback são retidos intencionalmente. Resolva a referência/do proprietário informada ou expanda o disco com segurança; nunca exclua recursivamente a raiz de runtime.

## O terminal no navegador não se reconecta após a reinicialização

Atualize uma vez, confirme que o servidor e a sessão tmux estão íntegros e verifique a conexão WebSocket do navegador por qualquer proxy reverso. Garanta que o Caddy ou outro proxy não esteja removendo cabeçalhos de upgrade. Um PWA instalado não armazena em cache o estado do terminal nem do WebSocket.

## Ainda com problemas

Retenha a menor evidência reproduzível e execute as verificações focadas de componentes antes de suítes amplas. Inclua apenas caminhos e mensagens seguros para uso público nos relatos de issues. Veja [Contribuindo](../CONTRIBUTING.md) para as expectativas de relato.
