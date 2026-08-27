---
slug: housekeeping
source: docs/HOUSEKEEPING.md
source_sha256: sha256:a7a64e54c6aaea5593617556363e4741ecb97759caa08e9d8bd3ba47d879927c
---
# Housekeeping

O Housekeeping recupera artefatos de runtime somente quando o ThreadCells consegue comprovar que eles são elegíveis. Ele é intencionalmente conservador: um recurso desconhecido, ilegível, ativo, referenciado ou alterado é protegido, em vez de se supor que seja seguro excluí-lo.

![Housekeeping do ThreadCells ao vivo, com integridade do disco, backups protegidos, agendamentos e política de limpeza](/media/screenshots/threadcells-housekeeping.webp)

## O que pode ser limpo

Dependendo da idade e das evidências de propriedade, um plano pode incluir:

- caminhos temporários expirados que possuem marcadores de propriedade do ThreadCells;
- anexos antigos de terminal sem referência por um terminal ativo;
- logs elegíveis para compactação ou limpeza por retenção;
- grupos de processos de navegador órfãos identificados pela identidade exata do processo;
- revisões e caches do navegador sem referência por metadados ativos;
- contêineres e volumes identificados pelo ThreadCells cujo proprietário está inativo e sem referência;
- caches de pacotes confiáveis com uma ação de recuperação mensurável;
- candidatos/releases inativos representados por metadados canônicos de staging.
- painéis de runtime de terminal exatamente encerrados e descendentes de processo cujo terminal durável já está encerrado e cuja identidade de processo ainda corresponde;
- worktrees gerenciados com limpeza pendente após o limite de resultado/retirada durável ser reconhecido e revalidado.
- worktrees vinculados, limpos e inativos cujo HEAD já está contido em uma referência Git durável explicitamente configurada;
- caches marcados e reproduzíveis/evidência gerada localizados diretamente sob uma raiz de cache aprovada, depois que seu proprietário está inativo e o período de retenção transcorreu.

O Housekeeping não exclui cegamente repositórios de código-fonte, worktrees ativos ou desconhecidos, terminais em execução, arquivos abertos, releases atual/para reversão, candidatos preparados nem backups. Worktrees vinculados são retirados por `git worktree remove` e `git worktree prune`, nunca por exclusão recursiva genérica. Retirar o runtime de um terminal encerrado não exclui seu histórico durável de sessão, agente, Inbox, resultado ou fluxo de trabalho.

Um diretório reproduzível precisa ser filho imediato de uma raiz configurada e conter `.threadcells-reproducible.json`:

```json
{"schema_version":1,"owner":"threadcells","kind":"cache","created_at":1790000000,"owner_pid":12345}
```

Os tipos aceitos são `cache`, `generated`, `test_evidence` e `candidate`. Marcadores ausentes ou inválidos, links simbólicos, escapes de caminho, proprietários ativos e caminhos dentro da janela de retenção permanecem protegidos.

As implantações também podem nomear prefixos de cache exatos pertencentes ao ThreadCells para caches de CI compatíveis com versões anteriores. Essas entradas permanecem restritas a filhos diretos da raiz aprovada pertencente ao runtime e exigem a retenção transcorrida mais as mesmas verificações de processo ativo e identidade no momento da execução. Prefixos não listados, incluindo artefatos ambíguos de candidato a release, permanecem protegidos.

## Primeiro planejar, depois executar

Um plano de simulação é somente leitura. Cada candidato inclui sua categoria, identidade/impressão digital canônica, ação proposta, total de bytes, estimativa de bytes recuperáveis quando conhecida, motivo de retenção e motivo de proteção. Os resumos por classe informam separadamente as áreas acionáveis/recuperáveis e as preservadas/protegidas, para que uma classe grande protegida não fique oculta como zero bytes.

```text
Inspect current state
      ↓
Build immutable plan and plan_id
      ↓ operator reviews
Execute exact plan_id
      ↓
Rebuild protected set under lock
      ↓
Revalidate each candidate immediately before action
      ↓
Report reclaimed, skipped, changed, and failed items
```

Se o conjunto de candidatos mudar entre o planejamento e a execução, a execução manual rejeitará o plano obsoleto sem alterar recursos. Cada candidato restante é verificado novamente imediatamente antes da mutação.

## Full Cleanup

A ação final da zona de perigo em Settings → Housekeeping é **Delete all system files — Full Cleanup**. Ela usa o mesmo inventário canônico, conjunto protegido, identidade imutável do plano e verificações de identidade no momento da execução que o Housekeeping normal, mas aplica a retenção máxima comprovadamente segura: caches reproduzíveis, logs antigos, artefatos de build/candidato/temporários, worktrees que podem ser aposentados com segurança e todas as releases locais inativas podem se tornar elegíveis. Propriedade desconhecida ou autoridade ambígua permanecem protegidas e são explicadas no plano e no relatório.

O Full Cleanup só fica disponível quando a verdade de ciclo de vida do backend comprova que cada agente relevante está Ready, Exited ou em outro estado explicitamente equivalente e sem execução. Os estados Working, Processing ou Starting, mutações do sistema de arquivos enfileiradas, execução de provedor, trabalho Heavy, operações de runtime e identidade de ciclo de vida desconhecida bloqueiam a execução. O servidor adquire os limites canônicos de admissão e verifica novamente essa barreira de ociosidade imediatamente antes da mutação; se um agente ficar ativo após a prévia, a execução é abortada sem excluir nada.

A prévia é somente leitura. A execução exige o desbloqueio de operador existente e de curta duração e o modal existente de confirmação de ação permanente; não há senha de Full Cleanup nem segredo armazenado pelo cliente. A solicitação confirma um `plan_id` exato de 64 caracteres e não carrega nenhum caminho arbitrário.

Cada candidato do Full Cleanup baseado em caminho é executado pelo root helper de escopo restrito, ativado por socket, depois que ele reautentica o operador de forma independente, reconstrói o plano exato, comprova a barreira de ociosidade e verifica que o plano de controle ainda mantém todos os limites de admissão. O helper move cada candidato para uma quarentena exclusiva de root no mesmo sistema de arquivos, bloqueia a árvore de diretórios capturada contra mutações pelo usuário de runtime e então exclui somente as identidades verificadas por meio de descritores de diretório. Uma identidade alterada é preservada e informada; a execução nunca recorre a uma exclusão de caminho mais fraca como usuário de runtime. Recursos de ciclo de vida que não pertencem ao sistema de arquivos continuam passando por seus executores transacionais canônicos.

Após um Full Cleanup bem-sucedido, somente a release local ativa e imutável do ThreadCells permanece. Todas as releases inativas comprovadas de rollback/recuperação são removidas, os metadados de release são reconciliados atomicamente e o rollback local é informado como indisponível. A release ativa e o ponteiro ativo nunca podem ser candidatos. Agentes Ready continuam utilizáveis: seus worktrees, autoridade de escrita, contexto atual, saída atual e demais estados de continuação ficam protegidos. O histórico Exited pode permanecer no SQLite depois que sua saída segura do sistema de arquivos for limpa; Full Output então informa que a saída durável está indisponível, em vez de falhar ou inventar texto.

Backups, autoridade atual de fontes/ferramentas, credenciais/estado do provedor, banco de dados SQLite e qualquer recurso não comprovado permanecem protegidos. Um segundo Full Cleanup produz com segurança um plano acionável quase nulo, exceto por itens recém-elegíveis ou anteriormente protegidos.

## Exemplo manual seguro

No ambiente instalado, primeiro solicite a saída JSON:

```bash
threadcells-housekeeping --dry-run --json
```

Revise cada candidato e copie o `plan_id` retornado. Execute somente esse plano inspecionado:

```bash
threadcells-housekeeping --plan-id PLAN_ID_FROM_DRY_RUN
```

Não crie scripts para extrair `plan_id` e executar imediatamente até compreender o plano. Uma simulação nunca implica autorização para excluir.

## Filosofia do conjunto protegido

O conjunto protegido combina terminais e worktrees ativos, responsabilidade de escrita/fluxo de trabalho, linhagem de código-fonte/runtime atual, releases ativos e de reversão, candidatos preparados, revisões de navegador referenciadas, arquivos abertos, identidade de início de processo em execução e identidade de terminal, metadados de referência de contêiner, backups e bloqueios compartilhados.

Os detalhes importam para a implementação, mas a regra para o operador é simples: **a ausência de evidência não é evidência de que um recurso está inativo**. Se a proteção não puder ser estabelecida com precisão, o Housekeeping ignora o recurso e informa o motivo.

A autoridade do fluxo de trabalho protegido é derivada da identidade durável do terminal raiz. A inicialização e a reconciliação frequente cancelam fluxos de trabalho órfãos que não estão em recuperação quando seu terminal raiz não existe mais e, em seguida, regeneram o conjunto protegido. Até que essa relação seja reconciliada, a retirada de worktree falha de forma fechada para todo o inventário incerto.

## Agendamentos

Settings → Housekeeping separa política, agenda, planejamento, execução e relatórios. As formas de agendamento compatíveis incluem:

- um intervalo frequente de 15 minutos a 365 dias, como `6h`;
- um agendamento semanal em UTC, como `Sun 04:00 UTC`;
- limpeza por pressão de disco usando `on_red`.

Os temporizadores instalados podem consultar a cada 15 minutos, com ativação inicial escalonada, para que as verificações frequente e semanal normalmente não colidam. Comprovantes duráveis impedem que uma classe de agendamento seja executada duas vezes antes de vencer. Uma consulta agendada que encontra o mecanismo canônico de Housekeeping já ativo é encerrada com êxito como ignorada e tenta novamente mais tarde; a contenção manual de bloqueio continua sendo um erro. Uma execução agendada cria e executa seu plano devido sob um único bloqueio de serviço; ela não reutiliza um plano manual aprovado por humano.

As alterações do Housekeeping e a execução manual são protegidas por [Autorização de operador](OPERATOR_AUTHORIZATION.md).

## Comportamento sob pressão de disco

Em YELLOW, inspecione o crescimento e execute um plano simulado. Em RED, o ThreadCells pode admitir uma concessão pesada de Housekeeping segura para recuperação, embora o trabalho pesado comum possa ser negado. Os planos de pressão ordenam primeiro os maiores candidatos comprovadamente seguros e mostram as classes protegidas dominantes, mas a limpeza ainda conta como uma execução pesada e não ignora nenhuma proteção de candidato.

YELLOW é um estado de inspeção, não permissão para fabricar bytes recuperáveis. Quando todas as classes grandes restantes estão protegidas, crie capacidade externa ou documente a área protegida, em vez de enfraquecer os predicados.

A recuperação de cache de pacotes é informada como desconhecida/zero quando o comando não consegue comprovar bytes; o ThreadCells não anuncia recuperação estimada.

## Relatórios e falha parcial

O relatório mais recente registra identidade de plano/execução, estado de recursos, estimativas, resultados reais, desfechos por candidato e códigos de motivo estáveis. A falha de um candidato não enfraquece a proteção de candidatos posteriores nem oculta sucessos independentes.

Após uma execução, verifique a pressão de disco e inspecione entradas ignoradas/com falha. Planeje novamente antes de outra execução; não reutilize um plano antigo após mudanças de estado.

## Backups e releases

Os backups são apenas de inventário. As decisões de retenção de mídia de backup pertencem à política de backup do operador, não ao Housekeeping automático.

A limpeza de releases e candidatos compartilha o bloqueio canônico de staging e exige metadados de referência confiáveis. O Housekeeping normal protege os runtimes ativo e de reversão. O Full Cleanup protege somente a release ativa e remove intencionalmente cada release local inativa de rollback comprovada após confirmação explícita do operador. Veja [Atualização](UPGRADING.md).

Os serviços instalados de Housekeeping agendado recebem o grupo restrito de manutenção de release necessário para recuperar um release imutável elegível. O plano de controle principal e os processos comuns de agentes não recebem esse grupo. Uma execução manual/API sem essa autoridade ignora a exclusão de release com `RELEASE_ADMIN_GROUP_REQUIRED`, continua a limpeza segura independente e deixa o serviço agendado recuperar o release mais tarde por meio do mesmo mecanismo de planejar/executar.

Os inventários de proteção de caminhos abertos incluem todos os processos pertencentes à conta de runtime configurada do ThreadCells, independentemente de qual conta autorizada invoque um plano manual. Outras contas do host estão fora do limite de propriedade do estado descartável do ThreadCells; entradas privadas e ilegíveis de `/proc` dessas contas não desativam a limpeza de todo o host. Uma identidade de runtime desconhecida ou qualquer incerteza ao inspecionar um processo da conta de runtime ainda falha de forma fechada.

## Erros comuns

- Excluir diretamente um diretório de worktree para recuperar espaço.
- Tratar uma contagem estimada de bytes como recuperação garantida.
- Executar um plano que não foi inspecionado.
- Supor que um PID interrompido é prova suficiente de que um grupo de navegador/processo é o antigo.
- Esperar que o Housekeeping exclua backups.
- Aumentar os limites de disco em vez de lidar com crescimento sustentado.
