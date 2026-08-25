---
slug: concepts
source: docs/CONCEPTS.md
source_sha256: sha256:1e0e6123a7e1d36cffc5d9bd3a7178930c5d355386e008093fc0209c5cf951e9
---

# Conceitos centrais

O ThreadCells acrescenta estrutura aos terminais nativos de agentes de programação. Esta página apresenta uma ideia de cada vez e depois mostra como as peças se encaixam.

## Agente

Um **agente** é uma CLI de provedor executada com um prompt, papel, perfil e contexto de projeto. Ele pode inspecionar arquivos, usar ferramentas, escrever código quando autorizado e retornar um resultado.

Um agente não é apenas o nome do modelo. Dois agentes podem usar o mesmo modelo e ter papéis, permissões, configurações de raciocínio e worktrees diferentes.

## Terminal

Um **terminal** é o ambiente de processo real apoiado por tmux no qual um agente é executado. Ele preserva a saída nativa do provedor e permite que o operador se reconecte após fechar o navegador.

O terminal pode encerrar enquanto o resultado durável permanece. Inversamente, a existência contínua de um terminal não prova que o trabalho útil ainda está avançando.

## Sessão

Uma **sessão** é a vida útil durável do ThreadCells para um grupo relacionado de execuções de agentes: identidade, ciclo de vida, terminais, provedores, perfis, projeto, uso e relações de resultados. **Add Agent** adiciona um terminal a essa vida útil exata da sessão, em vez de inferir associação por um nome exibido reutilizado. As sessões permitem que Statistics e os fluxos de trabalho raciocinem sobre execuções ativas, concluídas, históricas ou retidas.

## Projeto

Um **projeto** identifica o repositório Git ao qual o trabalho pertence. Ele dá ao ThreadCells um escopo estável para sessões, worktrees e resultados; não substitui remotos do Git nem permissões do repositório.

## Worktree gerenciado

Um **worktree gerenciado** é um Git worktree criado para um contexto de agente delimitado. Ele permite que trabalhadores paralelos operem em branches diferentes sem editar o mesmo checkout.

Worktrees reduzem colisões; eles não são sandboxes de segurança. Um agente ainda pode alcançar tudo que sua conta do sistema operacional puder alcançar.

## Autoridade de escrita

A **autoridade de escrita** responde quem pode modificar um contexto de trabalho específico. O ThreadCells mantém essa propriedade explícita para que dois agentes ativos de forma independente não sejam considerados acidentalmente escritores concorrentes seguros do mesmo worktree.

Um revisor normalmente precisa de acesso de leitura, mas não de autoridade de escrita. Um desenvolvedor que faz uma implementação precisa dela.

## Provedor

Um **provedor** conecta o ThreadCells a uma CLI nativa de agente de programação como Codex ou Claude Code. Três estados importam:

1. O ThreadCells contém um adaptador de provedor.
2. A CLI correspondente está instalada para o usuário de runtime.
3. Essa CLI está íntegra e autenticada o suficiente para iniciar.

O fato de um adaptador aparecer em Settings não implica que a CLI externa esteja instalada. Consulte [Provedores](PROVIDERS.md).

## Perfil

Um **perfil** é uma política de inicialização reutilizável. Ele seleciona provedor/modelo e nível de raciocínio, fornece instruções e capacidades, define um papel e pode restringir como um agente participa da orquestração.

Os perfis integrados oferecem papéis conhecidos e seguros. Perfis personalizados permitem que operadores adaptem esses papéis sem alterar o código do aplicativo.

## Supervisor e trabalhador

Um **supervisor** possui uma missão mais ampla. Ele pode dividi-la em tarefas delimitadas, enviá-las a trabalhadores, coletar seus resultados duráveis, solicitar revisão e decidir quando a missão está realmente concluída.

Um **trabalhador** ou **agente delegado** possui uma dessas tarefas delimitadas. Um trabalhador deve reportar suas evidências ao pai; ele não decide silenciosamente o resultado de nível superior.

```text
Owner
  ↓
Supervisor
  ├── Developer ── implementation result ──┐
  └── Reviewer  ── acceptance result ──────┤
                                           ↓
                              Supervisor incorporates results
                                           ↓
                                  Top-level completion
```

Um **supervisor residente** pode permanecer disponível enquanto os trabalhadores executam turnos. Sua residência consome uma vaga de supervisor mesmo quando o modelo não está produzindo saída.

## Fluxo de trabalho

Um **fluxo de trabalho** é o registro durável de coordenação de uma missão ou tarefa delegada. Ele acompanha quem possui o trabalho, qual entrada lógica é atual, se os resultados foram entregues e incorporados e se é necessária a conclusão ou uma decisão do proprietário.

A conclusão de um turno de provedor/modelo não é a conclusão do fluxo de trabalho. Um supervisor pode terminar um turno, receber posteriormente um resultado de trabalhador e continuar a mesma missão aberta.

## Resultado durável

Um **resultado durável** é a evidência estruturada de conclusão produzida pelo trabalho delegado. Ele pode incluir um resumo, arquivos alterados, verificações, riscos e bloqueadores. O ThreadCells o armazena e entrega mesmo se o terminal do trabalhador for aposentado depois.

Entrega não é o mesmo que incorporação. O supervisor reconhece um resultado somente depois de realmente usá-lo ou avaliá-lo.

## Controle do proprietário

Um **controle do proprietário** pausa a continuação autônoma porque a próxima decisão exige o proprietário humano — por exemplo, uma publicação, um novo limite de confiança externo, uma ação destrutiva irreversível ou uma decisão de produto que não havia sido autorizada antes.

O fim de um turno de modelo comum ou uma etapa difícil de implementação não é um controle do proprietário.

## Quatro tipos de capacidade

O ThreadCells separa quatro limites de capacidade porque eles restringem partes diferentes da máquina.

### Supervisor residente

Um supervisor ou proprietário de nível superior permanece disponível para receber callbacks e continuar seu fluxo de trabalho. A residência é diferente da execução ativa do modelo e da capacidade de trabalho delegado.

### Execução do provedor

O modelo está produzindo ativamente um turno. Cotas do provedor, limites de processo e atividade de rede restringem essa categoria.

### Contexto de trabalho

Um contexto de programação delegado possui trabalho no momento. Ele pode reter um worktree e autoridade de escrita mesmo enquanto espera por um comando ou callback.

### Execução pesada

Uma compilação, uma execução do Chromium, uma grande suíte de testes ou uma tarefa de host igualmente custosa ocupa uma vaga pesada. Pressão de CPU, memória e E/S restringe essa categoria.

Um supervisor residente pode esperar sem usar uma vaga de provedor, e um agente delegado pode manter um contexto de trabalho sem usar uma vaga de provedor nem pesada. Portanto, elevar todos os limites juntos pode sobrecarregar o host sem acelerar o fluxo de trabalho. Consulte [Capacidade e modelo de recursos](RESOURCE_MODEL.md).

## Um exemplo completo

Um proprietário inicia um supervisor para um repositório. O supervisor atribui a um desenvolvedor um worktree gerenciado e autoridade de escrita. O desenvolvedor usa uma execução de provedor ao gerar código e, depois, uma vaga pesada para a compilação de produção. Seu resultado durável retorna ao supervisor. Um revisor lê o worktree e relata uma regressão bloqueante. O supervisor inicia outro turno, pede ao desenvolvedor que a corrija, incorpora ambos os resultados e conclui explicitamente o fluxo de trabalho.

O terminal, a sessão, o worktree, o fluxo de trabalho e o resultado são separados porque cada um possui uma vida útil e uma verdade diferentes a preservar.

Próximo: [Fluxos de trabalho e resultados duráveis](WORKFLOWS_AND_RESULTS.md) transforma este vocabulário em um tutorial operacional.
