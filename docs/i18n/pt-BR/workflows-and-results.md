---
slug: workflows-and-results
source: docs/WORKFLOWS_AND_RESULTS.md
source_sha256: sha256:d6a1133dbc73417c1e5cfc8d6b96037535cf4b5552bd094cbb4efad93351fc0a
---

# Fluxos de trabalho e resultados duráveis

Um fluxo de trabalho representa trabalho que precisa permanecer coerente entre vários turnos de modelo, terminais ou agentes delegados. Ele impede que a mensagem final de um provedor seja confundida com a conclusão da missão maior.

![Sessão do ThreadCells ao vivo expandida mostrando participantes ativos e concluídos do fluxo de trabalho](/media/screenshots/threadcells-session-workflow.webp)

## Trabalho de nível superior e delegado

O **fluxo de trabalho de nível superior** pertence ao agente ou supervisor iniciado para a missão do proprietário. Um **fluxo de trabalho delegado** pertence a um filho que recebeu uma tarefa delimitada.

```text
Top-level: "Prepare the release candidate"
  ├── Delegated: "Fix the statistics parser"
  ├── Delegated: "Review operator authorization"
  └── Owner gate: "Approve public publication"
```

Cada fluxo de trabalho tem sua própria entrada lógica atual e estado de conclusão. Um trabalhador pode concluir seu fluxo de trabalho delegado enquanto o fluxo de trabalho de nível superior permanece aberto.

## Assign e handoff

**Assign** inicia trabalho delimitado independente e permite que o pai continue. O resultado do filho é entregue depois. Isso é útil para investigação, implementação ou revisão paralela.

**Handoff** transfere uma tarefa delimitada e aguarda seu resultado validado antes que o pai continue. É útil quando a próxima etapa do pai depende diretamente dessa resposta.

Ambas as formas preservam a identidade pai/filho e um resultado durável. Nenhuma concede a um filho autoridade de proprietário mais ampla que a explicitamente delegada pelo pai.

Uma negação transitória de admissão antes da inicialização, como capacidade de contexto de trabalho esgotada, é registrada como não admitida, e não como uma atribuição executada. O mesmo efeito lógico pode ser tentado novamente quando a capacidade estiver disponível; depois que a inicialização de um filho é admitida ou seu resultado se torna incerto, a proteção normal contra duplicação continua em vigor.

## Ciclo de vida do resultado

```text
Task admitted
   ↓
Child works
   ↓
Structured result recorded
   ↓
Result delivered to parent
   ↓
Parent reads and incorporates it
   ↓
Parent acknowledges incorporation
   ↓
Eligible child resources can retire
```

Um resultado normalmente inclui resumo conciso, arquivos alterados, verificações realizadas, riscos restantes e bloqueios. Isso é evidência operacional, não substitui examinar o diff ou a saída de testes.

A entrega ocorre pelo menos uma vez. Se o pai reiniciar antes de reconhecer um resultado entregue, o ThreadCells pode entregá-lo novamente. O pai deve usar a identidade imutável do resultado para evitar incorporar o mesmo trabalho duas vezes.

A entrega no Inbox é FIFO dentro de um terminal e vinculada ao fluxo de trabalho e turno lógico exatos que a criaram. Um transporte pendente é estado de entrega, não autoridade para mover uma carga útil ou resultado para outro fluxo de trabalho. Se seu fluxo de trabalho vinculado não estiver mais aberto, o ThreadCells finaliza esse transporte obsoleto e permite que trabalho mais recente de proprietário aberto prossiga sem religar a carga útil, fluxo de trabalho, entrega, recibo ou identidade de efeito. A mesma reconciliação é executada após reinicialização e é idempotente.

## Conclusão do provedor versus conclusão do fluxo de trabalho

Um turno de provedor termina sempre que o modelo devolve o controle. A missão ainda pode ter trabalho elegível: outro teste, um filho pendente, uma passagem de correção ou uma etapa de implantação.

Por isso, o ThreadCells mantém um fluxo de trabalho de nível superior aberto até que ocorra um destes resultados explícitos:

- a missão autorizada pelo proprietário está concluída;
- um bloqueio de proprietário é realmente necessário;
- o proprietário a cancela;
- uma falha irrecuperável real esgota seu caminho de recuperação delimitado.

Finais comuns repetidos de provedores usam continuação durável, um turno de cada vez, com backoff limitado. O ThreadCells continua admitindo o próximo turno lógico enquanto o fluxo de trabalho está aberto. Se um provedor se estabiliza diretamente em Ready em vez de expor um frame concluído repetível, o ThreadCells faz debounce durável desse estado após a reinicialização e avança o mesmo fluxo de trabalho aberto; uma observação posterior de Processing cancela um candidato Ready transitório. Entrada direta do proprietário e resultados duráveis de filhos redefinem o contador de ausência de progresso. Como proteção contra loops pagos, 65 finais consecutivos sem progresso durável colocam o fluxo de trabalho em um bloqueio explícito e visível ao proprietário. A conclusão do provedor nunca se torna conclusão da missão, e a continuação autônoma normal não exige uma ativação do proprietário.

## Bloqueios do proprietário

Use um bloqueio do proprietário quando a próxima etapa precisar de autoridade que a missão não concedeu. Bons exemplos incluem publicar em um remoto público, expor um novo serviço de rede, pagar por um recurso ou escolher entre semânticas de produto materialmente diferentes.

Não use um bloqueio do proprietário apenas porque o trabalho está lento, um teste falhou ou um turno de provedor terminou. Primeiro continue qualquer trabalho independente elegível.

## Recuperação

Na reinicialização, o ThreadCells reconstrói a propriedade do fluxo de trabalho a partir do estado durável. Resultados entregues, mas não reconhecidos, permanecem disponíveis. Um handoff em espera pode ser retomado contra o mesmo filho em vez de iniciar um duplicado. Quando um turno lógico mais novo é admitido para um fluxo de trabalho aberto, uma continuação pendente mais antiga é duravelmente substituída e não pode depois ser repetida como trabalho independente após compactação ou interrupção.

Se a execução do provedor/modelo for interrompida depois que sua entrada lógica foi admitida, mas antes que o trabalho necessário termine, o ThreadCells retoma por um novo turno de continuação durável em vez de repetir o recibo original. Efeitos concluídos permanecem isolados, a propriedade da execução de provedor segue o turno retomado, e o mesmo resultado imutável de filho e barreira de conclusão permanecem disponíveis para incorporação e reconhecimento exatamente uma vez.

Conclusão direta, falha, cancelamento, bloqueio do proprietário, finalização do filho e cancelamento central do fluxo de trabalho protegido isolam transportes pendentes do Inbox na mesma transação de banco de dados. Isso impede que uma transição de terminal deixe estado de entrega comum que possa suprimir um turno posterior do proprietário.

Uma reinicialização do serviço com a mesma compilação mantém compatível a conexão de controle do lado do provedor. Depois que uma compilação promovida muda código privilegiado de orquestração, uma conexão antiga é isolada antes que possa criar um efeito. Se a identidade ativa estiver temporariamente indisponível durante a reinicialização, a operação será rejeitada sem efeito e tentada novamente depois que o serviço retornar. Para Codex, o ThreadCells vincula a conversa exata do provedor ao terminal gerenciado e à geração de runtime na prontidão de inicialização e persiste essa identidade como autoridade de reconexão. Outros arquivos de rollout abertos não podem tornar esse terminal gerenciado ambíguo. Uma identidade ausente, obsoleta, errada ou impossível de comprovar falha de modo fechado antes do despacho ao provedor. A identidade de retomada durável torna segura uma reinicialização do serviço mesmo entre saída e reinicialização. Transporte de entrada, reconexão e retirada compartilham uma única reivindicação de mutação durável por terminal, portanto o texto não pode ser colado na lacuna do shell de reconexão e uma reconexão obsoleta não pode reiniciar depois que a retirada vence. O turno lógico já durável é tentado novamente, e não substituído.

Se um terminal desaparecer, inspecione os registros de fluxo de trabalho e de resultado antes de tentar novamente. Um novo terminal não pode duplicar silenciosamente uma mutação já concluída pelo antigo.

## Exemplo concreto

1. O proprietário inicia um supervisor para adicionar um recurso e validá-lo.
2. O supervisor atribui a implementação a um desenvolvedor e continua inspecionando testes.
3. O desenvolvedor faz commit da alteração e registra um resultado.
4. O ThreadCells o entrega; o supervisor lê o diff e reconhece a incorporação.
5. O supervisor atribui um revisor independente.
6. O revisor encontra uma regressão bloqueadora no navegador e registra evidências.
7. O supervisor continua o mesmo fluxo de trabalho aberto de nível superior, solicita uma correção e executa novamente a aceitação.
8. Somente após a compilação aceita e a implantação autorizada o supervisor conclui explicitamente o fluxo de trabalho.

Nas etapas 3, 4 e 6, turnos individuais do modelo terminaram. A missão não.

## Erros comuns

- Tratar uma mensagem final do terminal como conclusão de nível superior.
- Reconhecer um resultado antes de lê-lo ou usá-lo.
- Iniciar um filho substituto sem verificar um resultado durável anterior.
- Deixar dois filhos alterarem o mesmo worktree.
- Usar um bloqueio do proprietário como botão de pausa genérico.

Consulte [Projetos e worktrees gerenciados](PROJECTS_AND_WORKTREES.md) para isolamento de escrita e [Modelo de capacidade e recursos](RESOURCE_MODEL.md) para limites de admissão.
