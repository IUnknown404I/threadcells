---
slug: overview
source: docs/OVERVIEW.md
source_sha256: sha256:c4082c5da946df3936a8eb1c711b4701ba75b6dfa5000d82bc5f5416d8322f3e
---

# Comece aqui: o que é o ThreadCells?

O ThreadCells é um sistema auto-hospedado para executar vários agentes de programação como um único fluxo de trabalho coordenado em uma máquina Linux. Ele oferece aos agentes terminais reais e Git worktrees, mantém missões abertas em andamento entre turnos do modelo e deixa o operador no controle da capacidade, do acesso de escrita, das mudanças protegidas e do resultado final.

Se você sabe usar Git, SSH e um agente de programação de linha de comando, já tem a base necessária para começar. Não é preciso entender a arquitetura interna do ThreadCells antes de iniciar um trabalho útil.

## Por que usá-lo?

Um único terminal de agente de programação é fácil de entender. Vários terminais tornam-se mais difíceis: dois agentes podem editar a mesma branch, uma compilação pode esgotar a memória, um supervisor pode desaparecer antes de coletar uma revisão e o fim de um terminal não significa necessariamente que a missão solicitada foi concluída.

O ThreadCells torna essas relações explícitas e mantém seu próprio ambiente operacional. Ele é especialmente útil quando você quer:

- manter agentes de longa execução visíveis e reconectáveis;
- dar a trabalhadores paralelos worktrees gerenciados e separados;
- permitir que um supervisor delegue implementação e revisão;
- receber resultados e mensagens do Inbox sem copiá-los manualmente entre terminais;
- continuar uma missão lógica entre turnos do provedor e reinicializações normais;
- limitar separadamente os turnos do modelo, o trabalho ativo e as tarefas pesadas do host;
- preservar resultados mesmo depois que um terminal encerra;
- monitorar a pressão do host e limpar com segurança resíduos descartáveis de runtime, logs, cache, compilação e lançamento do ThreadCells;
- exigir uma decisão do proprietário antes de uma etapa sensível ou ambígua.

O ThreadCells foi projetado para um operador confiável ou uma pequena equipe confiável em um host que controlam. Ele não é um sandbox multi-inquilino hostil.

## O ciclo básico

```text
Create a session and choose a project and agent
        ↓
Give the agent or supervisor the job
        ↓
Watch the coordinated workflow and host state
        ↓
ThreadCells continues eligible work across model turns
        ↓
Step in only for an explicit owner decision or final review
```

O agente continua sendo executado pela CLI nativa do provedor. O ThreadCells coordena o trabalho ao redor dela; não substitui o provedor. O Housekeeping protege o trabalho ativo, o estado durável, os backups e os lançamentos atual/de recuperação, e recupera somente candidatos cuja propriedade e elegibilidade possam ser comprovadas. Isso reduz a supervisão manual dos resíduos do ThreadCells, mas não garante que o host físico jamais falhe.

## Uma primeira hora produtiva

1. Siga a [configuração rápida](../QUICK_SETUP.md) para compilar e verificar um candidato local.
2. Use [Instalação](INSTALLATION.md) se quiser entender a razão de cada etapa ou precisar de ajuda com pré-requisitos.
3. Siga [Seu primeiro projeto e agente](FIRST_AGENT.md).
4. Leia [Conceitos centrais](CONCEPTS.md) depois de ver um agente funcionando.
5. Antes de usar outra máquina, escolha um método seguro em [Acesso remoto](REMOTE_ACCESS.md).

Depois disso, [Provedores](PROVIDERS.md), [Perfis](PROFILES.md) e [Fluxos de trabalho e resultados duráveis](WORKFLOWS_AND_RESULTS.md) explicam o principal modelo operacional. [Operações](OPERATIONS.md) cobre as verificações rotineiras para manter uma instalação saudável.

## O que o ThreadCells não faz

Os worktrees do ThreadCells organizam escritas; eles não isolam um agente do host em sandbox. O ThreadCells também não adiciona uma proteção geral de login à Web UI. Mantenha o servidor somente em loopback e use encaminhamento SSH ou um proxy reverso autenticado para acesso remoto.

A versão atual é uma prévia técnica. Leia [Modelo de segurança](SECURITY_MODEL.md) e [Limitações](LIMITATIONS.md) antes de colocar repositórios valiosos sob o controle de agentes.

## Criador e mantenedor

O ThreadCells foi criado e é mantido por [Subaev Ruslan](https://github.com/IUnknown404I), com contribuições da comunidade ThreadCells. Ele nasceu da necessidade prática de operar vários agentes de programação CLI nativos com controle operacional mais sólido, resultados duráveis e segurança de recursos.
