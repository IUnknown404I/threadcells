---
slug: resource-model
source: docs/RESOURCE_MODEL.md
source_sha256: sha256:6eaee9cd5449ab9af23c8a9ce5b6687e73b301bb445da3e35de369a65fa05bb2
---

# Modelo de capacidade e recursos

O ThreadCells separa a capacidade porque o trabalho de agentes de programação pode pressionar partes diferentes de um host em momentos distintos. Um turno de modelo consome capacidade do provedor; um contexto de programação atribuído pode permanecer ativo enquanto o modelo está ocioso; uma compilação pode saturar a máquina depois que a saída do modelo cessou.

![Capacidade de orquestração ao vivo mostrando limites independentes de residentes, provedores, trabalho e tarefas pesadas](/media/screenshots/threadcells-capacity.webp)

Aumentar todos os números juntos geralmente não é mais rápido. Isso pode criar contenção pela cota do modelo, pressão de memória, atividade intensa em disco e diversas compilações caras disputando a mesma CPU.

## Os quatro limites

### Supervisores residentes

Uma vaga residente mantém um supervisor de nível superior ou sessão do proprietário que deve permanecer disponível durante delegação e callbacks. Ela consome residência mesmo enquanto espera o resultado de um trabalhador.

Isso é separado porque encerrar um supervisor que parece ocioso pode perder o contexto responsável por integrar a missão.

### Execuções de provedor

Uma vaga de execução de provedor é usada enquanto um modelo/provedor produz ativamente um turno. As restrições relevantes são concorrência do provedor, atividade de rede, contagem de processos e, às vezes, memória.

Um agente aguardando em um prompt não precisa de uma vaga de execução de provedor.

### Contextos de trabalho

Uma vaga Work representa um trabalhador ou revisor delegado que atualmente possui um contexto delimitado. Ela pode manter um worktree gerenciado e autoridade de escrita enquanto espera entre turnos de modelo.

A raiz de uma sessão de nível superior consome capacidade residente, não capacidade Work. Um filho delegado residente consome capacidade Work.

### Execuções pesadas

Uma vaga Heavy é para trabalho intenso no host, como uma compilação de produção, execução do Chromium, grande suíte de testes ou varredura em todo o repositório. A admissão Heavy protege a folga de CPU, memória e I/O.

Use o executor canônico de tarefas pesadas para os comandos que se qualificam. Testes pequenos comuns e inspeção de arquivos não precisam de uma vaga Heavy.

## O ponto de partida padrão

A configuração empacotada `5 resident / 3 provider / 2 Work / 1 Heavy` é um ponto de partida conservador para um host pequeno, não um benchmark nem limite fixo do produto.

Os intervalos permitidos são de 2 a 50 vagas residentes e de 1 a 50 para cada outro limite. Os valores persistem no banco de dados de runtime e entram em vigor sem reiniciar o servidor.

## O que devo configurar na minha máquina?

Comece de forma conservadora, observe pressão de memória/disco e filas e altere um limite por vez. Estes exemplos ilustram a forma; não são garantias de desempenho.

| Exemplo de host | Residentes | Provedor | Work | Heavy | Justificativa |
| --- | ---: | ---: | ---: | ---: | --- |
| VPS pequeno | 2 | 1 | 1 | 1 | Um supervisor e um filho delimitado; serialize o trabalho caro. |
| Estação de trabalho de desenvolvedor | 5 | 3 | 2 | 1 | Turnos paralelos de modelo úteis mantendo as compilações serializadas. |
| Host compartilhado maior | 8 | 5 | 4 | 2 | Mais missões e trabalhadores residentes, com folga medida para duas tarefas pesadas. |

Antes de aumentar um limite, pergunte qual fila realmente bloqueia o progresso:

- Provedor cheio, mas CPU ociosa: considere mais uma vaga Provider se as cotas permitirem.
- Work cheio com capacidade de provedor ociosa: retire filhos concluídos e reconhecidos ou aumente Work com cautela.
- Heavy cheio durante compilações: uma segunda vaga Heavy ajuda somente se CPU, RAM e disco suportarem compilações simultâneas.
- Resident cheio: encerre sessões concluídas de nível superior; não disfarce supervisores abandonados apenas elevando o limite.

## Pressão de memória e disco

O ThreadCells observa a pressão do host junto com as contagens configuradas. Muitas CLIs nativas, painéis tmux, processos de navegador, worktrees, caches de compilação e logs podem sobreviver ao curto turno de provedor que os criou.

O estado do disco usa limites exatos:

- **GREEN:** menos de 70% usado.
- **YELLOW:** de 70% a menos de 85%.
- **RED:** de 85% a menos de 92%.
- **CRITICAL:** 92% ou mais. A admissão agregada permanece RED e inclui o
  motivo `DISK_CRITICAL`, enquanto a projeção específica de disco informa CRITICAL.

YELLOW é um aviso para inspecionar o crescimento e planejar Housekeeping. RED pode negar novo trabalho arriscado e admitir limpeza segura para recuperação. Estado desconhecido falha de modo fechado; o ThreadCells não presume que um sistema de arquivos ilegível está saudável.

## Drenagem após uma redução

Reduzir um limite nunca mata trabalho ativo. Se o uso atual estiver acima do novo valor, essa categoria entra em **draining** e nega novas admissões até que o uso ativo fique dentro do limite.

Exemplo: mudar Work de 4 para 2 enquanto três filhos estão ativos deixa os três em execução. Conforme os filhos concluem e são retirados, nenhuma substituição é admitida até o uso chegar a 2 ou menos.

O inventário Heavy continua contando vagas ativas de números maiores após uma redução, portanto uma mudança de limite não pode ocultar um processo caro.

## Quando a capacidade é liberada

- A capacidade de Provider é liberada quando termina o turno ativo do modelo.
- A capacidade Heavy é liberada quando o comando pesado registrado termina.
- A capacidade Work é liberada somente depois que o contexto delegado é retirado com segurança.
- A capacidade Resident é liberada quando a sessão de supervisor/proprietário de nível superior é encerrada.

O resultado de um filho concluído precisa ser registrado, entregue, incorporado e reconhecido antes da retirada de recursos. O histórico permanece após a liberação da capacidade de runtime.

A admissão é verificada novamente nos limites de inicialização e continuação. Um turno de provedor enfileirado começa quando uma vaga de provedor fica disponível. A conclusão do provedor libera somente a capacidade de execução do provedor; ela não encerra um fluxo de trabalho aberto, descarta seu callback nem libera um contexto Work delegado que ainda possui trabalho durável.

## Configurar e observar

Use Settings → Orchestration Capacity para uso atual, limites, recomendações e estado de drenagem. As mudanças de capacidade são protegidas por [Autorização do operador](OPERATOR_AUTHORIZATION.md) e auditadas.

A visualização de status na linha de comando é:

```bash
threadcells-resource-status
```

Após uma alteração, verifique que UI e CLI concordam. Um limite é um controle de admissão, não uma promessa de throughput nem um sandbox de carga de trabalho.

## Erros comuns

- Aumentar todos os limites porque uma compilação está lenta.
- Contar um worktree ocioso como execução de provedor.
- Esquecer supervisores residentes ao dimensionar missões de longa duração.
- Reduzir um limite e esperar que tarefas ativas sejam encerradas.
- Tratar capacidade GREEN como prova de que há cotas de provedor disponíveis.
- Excluir arquivos de runtime para liberar uma vaga em vez de retirar com segurança o fluxo de trabalho proprietário.

Consulte [Housekeeping](HOUSEKEEPING.md) para recuperação de disco e [Fluxos de trabalho e resultados duráveis](WORKFLOWS_AND_RESULTS.md) para retirada segura de filhos.
