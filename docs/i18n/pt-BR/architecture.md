---
slug: architecture
source: docs/ARCHITECTURE.md
source_sha256: sha256:0fa43fdddc696e3203367cd85ab6b0ca6ec9d4c03753ebdfea3fc1d336507447
---
# Arquitetura

O ThreadCells é um plano de controle local em torno de processos nativos de agentes de programação. Ele mantém deliberadamente o terminal do provedor, o repositório Git, o estado durável de coordenação e a interface de navegador como componentes separados, com limites explícitos.

Comece por [Conceitos fundamentais](CONCEPTS.md) se os termos abaixo forem novos para você.

## Visão do sistema

```text
Browser or installed PWA
        ↓ HTTP / WebSocket on loopback
FastAPI ThreadCells server
  ├── SQLite durable state
  ├── provider/profile registries
  ├── workflow and result service
  ├── capacity and Housekeeping service
  └── tmux/provider adapter control
               ↓
        Native provider CLIs
               ↓
      Git repositories/worktrees
```

## Servidor e interface Web

O servidor FastAPI expõe a aplicação/API e disponibiliza uma compilação Web de produção. A interface React lê o estado operacional em tempo real e se conecta aos fluxos de terminal por WebSockets.

O worker básico da PWA armazena em cache somente ativos estáticos com impressão digital. HTML, APIs, autorização, sessões, fluxos de trabalho, Statistics, terminais, mutações e WebSockets continuam dependentes da rede, para que a interface não possa inventar um estado offline do plano de controle.

O pacote de Docs é gerado no momento da compilação a partir de `DOCS_MANIFEST.json`. Somente Markdown público incluído na lista de permissões entra no runtime.

## Estado durável

O SQLite mantém sessões, terminais, projetos, revisões de perfil/provedor, concessões de recursos, fluxos de trabalho, resultados, registros de uso, eventos de auditoria e comprovantes de agendamento. Operações que precisam ser exatamente uma vez ou seguras para repetição usam identidades estáveis e transações de banco de dados, em vez de depender de saída transitória do terminal.

Os processos de provedor e as sessões tmux são fatos externos de runtime. A inicialização/recuperação os reconcilia com o banco de dados; ela não deve pressupor que a existência de um lado prova que o outro está atualizado.

## Execução de provedores

Um adaptador traduz um lançamento normalizado do ThreadCells em uma invocação de CLI nativa revisada. O provedor continua renderizando sua própria interface de terminal e mantendo sua própria autenticação. Os adaptadores informam as capacidades e a realidade do preflight, em vez de simular comportamentos sem suporte.

A telemetria estruturada do provedor é normalizada em registros duráveis de uso. Contadores cumulativos usam pontos de controle estáveis, para que sondagens e reinicializações não dupliquem os totais.

## Contextos de trabalho Git

Worktrees gerenciados compartilham o banco de objetos do repositório, mas isolam caminhos de checkout e ramificações. A autoridade de escrita mantém explícita a responsabilidade pelas mutações. Worktrees são ferramentas de concorrência, não sandboxes do sistema operacional.

## Fluxos de trabalho e resultados

O estado do fluxo de trabalho sobrevive a turnos individuais do provedor. Resultados delegados são registrados, entregues pelo menos uma vez, incorporados pelo pai e confirmados antes de o filho se tornar elegível para retirada. A conclusão explícita — e não o encerramento do modelo — fecha a missão de nível superior.

## Admissão e pressão

Supervisores residentes, execuções de provedores, contextos de trabalho e execuções pesadas têm concessões e limites independentes. A pressão de disco e a proteção do Housekeeping são restrições adicionais de runtime. Cercas entre processos garantem que dois processos não possam ambos acreditar que adquiriram a última vaga.

## Limite de segurança

O ThreadCells pressupõe um único host e ambiente de operador confiáveis. O acesso geral à interface é protegido externamente por loopback/SSH ou por um proxy reverso autenticado. Mutações sensíveis de Settings usam um limite distinto de verificador/sessão de operador, mas isso não é um sistema geral de login.

Pacotes de provedores e CLIs nativas são código executável confiável. A configuração importada é dado declarativo restrito. Veja [Modelo de segurança](SECURITY_MODEL.md).
