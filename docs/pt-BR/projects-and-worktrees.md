---
slug: projects-and-worktrees
source: docs/PROJECTS_AND_WORKTREES.md
source_sha256: sha256:c296e8fec6654451a29dbef47bde79d19fda28f7bbfdf926c857e2cc8508ad3a
---

# Projetos e worktrees gerenciados

Um projeto do ThreadCells é um repositório Git registrado e a autoridade canônica do código-fonte. Ele dá a sessões, perfis, estatísticas e fluxos de trabalho um lugar estável ao qual pertencer, mas não é o diretório gravável normal de um novo supervisor. O ThreadCells nunca torna um repositório seguro apenas por registrá-lo; portanto, comece com um status limpo e entenda o limite de escrita que você concede.

## Registrar um projeto

Use o seletor de projetos em Spawn Agent para escolher um repositório existente ou adicione o repositório pelo controle de projeto compatível. Use um caminho canônico absoluto e confirme que o usuário de runtime do ThreadCells consegue lê-lo.

Antes do primeiro agente:

```bash
git -C /path/to/project status --short
git -C /path/to/project worktree list
```

Resultado esperado: você consegue distinguir alterações e worktrees preexistentes de tudo que o ThreadCells criar depois. Trabalho existente sem commit pertence ao operador; agentes não devem descartá-lo.

## Por que existem worktrees gerenciados

Dois escritores em um checkout podem sobrescrever as alterações um do outro mesmo que seus prompts não tenham relação. Um Git worktree gerenciado dá a cada escritor delimitado seu próprio checkout e branch, enquanto compartilha o banco de objetos do repositório.

```text
Canonical repository
  ├── operator checkout
  ├── Session A supervisor worktree
  ├── Session B supervisor worktree
  ├── developer worktree
  └── reviewer worktree or read-only context
```

O ThreadCells registra essa relação em vez de tratar diretórios temporários como anônimos. Isso torna a limpeza e a atribuição de resultados mais seguras.

Cada nova Sessão de supervisor associada a um Projeto, incluindo a primeira, recebe um worktree gerenciado e uma branch exclusivos em uma revisão base registrada exatamente. Uma segunda Sessão no mesmo Projeto recebe outro worktree; a capacidade residente continua global. Uma Sessão ainda tem um único supervisor principal, e um contexto gravável/worktree ainda tem no máximo um lease de escrita. A substituição de um supervisor inutilizável no mesmo contexto usa um recovery takeover explícito e preserva o worktree desse contexto em vez de criar um independente.

Sessões legacy ativas anteriores a este contrato permanecem no workspace existente. O ThreadCells não move, redefine, limpa, guarda com stash nem copia o estado sujo delas durante a atualização; Sessões novas usam worktrees gerenciados.

## Autoridade de escrita

Somente o contexto que detém a autoridade de escrita deve modificar um worktree gerenciado. Revisores podem inspecionar diffs e executar verificações seguras sem se tornarem um segundo escritor não rastreado.

Não edite manualmente um worktree gerenciado enquanto o agente dele estiver ativo. Se uma intervenção de emergência for necessária, primeiro pare ou coordene com o escritor e registre o que mudou.

## Recuperando o trabalho

Um resultado durável deve nomear os arquivos alterados e as verificações, mas o Git continua sendo a fonte de verdade do código. Revise o status, o diff e os commits do worktree antes de fazer merge ou cherry-pick pelo processo normal do repositório.

O ThreadCells não concede autoridade de publicação. Um resultado de trabalhador bem-sucedido não autoriza push, tag, implantação ou reescrita de histórico.

## Limpeza

O Housekeeping remove um worktree gerenciado apenas quando consegue provar que o worktree já não está protegido por um terminal ativo, fluxo de trabalho, lease de escrita ou resultado não incorporado. Propriedade desconhecida falha de modo fechado.

Se o uso de disco estiver alto, planeje o Housekeeping primeiro. Não exclua diretamente um diretório de worktree; isso pode deixar os metadados do Git e o estado do ThreadCells inconsistentes.

## Erros comuns

- Começar em um repositório sujo sem registrar as alterações existentes.
- Dar a dois agentes autoridade de escrita sobre o mesmo checkout.
- Tratar um worktree como sandbox de segurança.
- Excluir um worktree antes de incorporar seu resultado e commits.
- Presumir que uma branch gerenciada é mesclada ou publicada automaticamente.

Consulte [Fluxos de trabalho e resultados duráveis](WORKFLOWS_AND_RESULTS.md) para saber como os resultados de um worktree chegam a um supervisor.
