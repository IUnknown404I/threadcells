---
slug: first-agent
source: docs/FIRST_AGENT.md
source_sha256: sha256:0695738b5c690bf05b93bbd5a0afd0e1ab38857a7f488141af3244ce66dae948
---

# Seu primeiro projeto e agente

Este tutorial inicia um agente deliberadamente pequeno e mostra onde encontrar seu terminal e resultado. Primeiro conclua a [configuração rápida](../QUICK_SETUP.md) e deixe o servidor ThreadCells em execução.

## 1. Prepare um repositório seguro

Use um repositório Git descartável ou limpo para a primeira execução. O ThreadCells identifica um projeto pelo repositório e pode criar worktrees gerenciados ao lado dele.

```bash
mkdir -p /tmp/threadcells-first-project
cd /tmp/threadcells-first-project
git init
printf '# First project\n' > README.md
git add README.md
git commit -m 'Create first project'
```

Resultado esperado: `git status --short` não mostra nada. Começar limpo torna fáceis de inspecionar as alterações do agente.

## 2. Abra o ThreadCells

Abra `http://127.0.0.1:9889` na máquina que executa o ThreadCells. Se o host for remoto, primeiro configure o túnel SSH descrito em [Acesso remoto](REMOTE_ACCESS.md).

Abra **Spawn Agent**, selecione o repositório como projeto e escolha um provedor instalado. Um provedor marcado como **CLI not installed** não pode iniciar; consulte [Provedores](PROVIDERS.md) se o provedor esperado não estiver disponível.

Escolha um perfil de trabalhador de uso geral para esta primeira tarefa. Escreva um prompt delimitado como:

```text
Add a short Usage section to README.md. Do not change any other file.
Run git diff --check and report the changed file.
```

Inicie o agente.

## 3. Observe o terminal

O novo agente aparece em **Agents**. Seu terminal é uma sessão tmux real, portanto a saída nativa do provedor continua visível e reconectável. O ThreadCells registra em torno desse terminal a identidade do projeto, perfil, provedor e sessão.

Resultado esperado: o estado muda de starting para running, a saída do provedor aparece e a capacidade reflete uma execução ativa do provedor enquanto o modelo produz um turno.

Se o agente nunca iniciar, verifique o rótulo de disponibilidade do provedor e os cartões de capacidade. [Solução de problemas](TROUBLESHOOTING.md) contém verificações por sintoma.

## 4. Inspecione o trabalho

Quando o agente terminar, inspecione seu resultado durável e o diff do repositório. Um terminal chegar a uma mensagem final do provedor é evidência, mas não dá permissão para mesclar, publicar ou implantar.

```bash
cd /tmp/threadcells-first-project
git status --short
git diff -- README.md
```

Se o agente trabalhou em um worktree gerenciado, use o caminho de worktree mostrado pelo ThreadCells em vez do caminho do repositório original. O worktree mantém escritores concorrentes separados até que seus commits sejam reconciliados deliberadamente.

## 5. Experimente a supervisão

Quando um único trabalhador fizer sentido, inicie um perfil de supervisor em outra tarefa pequena. Peça-lhe para atribuir uma tarefa de implementação e uma revisão independente. A relação deve parecer assim:

```text
Owner
  └── Supervisor
        ├── Developer
        └── Reviewer
              ↓
        Durable results return to the supervisor
```

O supervisor continua responsável por incorporar esses resultados e concluir o fluxo de trabalho de nível superior. O término de um trabalhador não fecha a missão do supervisor.

## Próximas etapas

- Aprenda os nomes usados na UI: [Conceitos centrais](CONCEPTS.md).
- Entenda os perfis antes de criar outros personalizados: [Perfis](PROFILES.md).
- Aprenda como a delegação sobrevive ao término do terminal: [Fluxos de trabalho e resultados duráveis](WORKFLOWS_AND_RESULTS.md).
- Dimensione a máquina de modo conservador: [Capacidade e modelo de recursos](RESOURCE_MODEL.md).
