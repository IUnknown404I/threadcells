---
slug: limitations
source: docs/LIMITATIONS.md
source_sha256: sha256:b2b9aa6344d8b7d6c1b1391b1022817b51ce189237616721475510e23dd29cd5
---

# Limitações atuais

O ThreadCells é uma prévia técnica focada em operações locais confiáveis para agentes de programação em um host Linux. Esses limites são fatos intencionais do produto, não promessas sobre recursos empresariais não implementados.

## Plataforma e escala

- A linha de base do host compatível é Ubuntu/Debian Linux.
- Um plano de controle local e banco de dados SQLite coordenam uma frota moderada em um host.
- Limites de capacidade reduzem a contenção, mas não criam contêineres rígidos de CPU/memória nem garantem throughput.
- Instalações muito grandes, multi-host, altamente disponíveis ou escaladas horizontalmente estão fora do contrato atual.

## Confiança e isolamento

- Agentes nativos são executados com o acesso de sistema operacional do usuário de runtime.
- Worktrees isolam checkouts Git, não a segurança de sistema de arquivos ou rede.
- Adaptadores de provedores são pacotes executáveis confiáveis.
- O sistema não foi projetado para multitenancy hostil ou inscrição pública não confiável.

## Acesso pela Web

- A UI comum não tem login geral de usuário integrado.
- O servidor deve permanecer somente em loopback, a menos que esteja protegido por um proxy HTTPS externo autenticado.
- A autorização do operador protege configurações sensíveis; ela não substitui o controle de acesso externo.
- A PWA instalável depende da rede e não fornece controle offline de agentes.

## Provedores e telemetria

- A disponibilidade de adaptadores integrados varia conforme a instalação da CLI, compatibilidade e autenticação do provedor.
- Alguns provedores não conseguem informar o estado de autenticação de maneira não interativa.
- Campos de uso existem apenas quando o provedor fornece telemetria verdadeira.
- Statistics é telemetria operacional, não uma fatura; desconhecidos históricos permanecem desconhecidos.

## Recuperação e automação

- A recuperação reconcilia o estado durável com processos externos tmux/provedor, mas não pode tornar reversível um comando externo não idempotente.
- Backups e restauração exigem disciplina do operador e devem ser ensaiados.
- O Housekeeping intencionalmente deixa artefatos ambíguos no lugar.
- Automação de publicação e lançamento remoto intencionalmente não faz parte da implantação local comum.

Avalie primeiro o ThreadCells em repositórios não críticos, mantenha backups verificados e inspecione a saída dos agentes antes de ações consequentes.
