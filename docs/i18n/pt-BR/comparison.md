---
slug: comparison
source: docs/COMPARISON.md
source_sha256: sha256:43b9f58d4b33db88b6d6271456b89ce9008759f75a7bcf1dbb7ce02657a1ccee
---

# Onde o ThreadCells se encaixa

O ThreadCells é para desenvolvedores que já valorizam CLIs nativas de agentes de programação, mas precisam de uma forma mais clara de operar várias delas em uma máquina.

## Comparado a janelas de terminal separadas

Shells tmux separados são simples, mas não registram automaticamente a identidade de perfil/provedor, propriedade de escritor gerenciado, admissão de capacidade, ancestralidade do fluxo de trabalho, resultados duráveis de filhos ou gates do operador. O ThreadCells preserva os terminais nativos enquanto adiciona esses registros operacionais.

## Comparado a uma plataforma de agentes hospedada

O ThreadCells é auto-hospedado e prioriza loopback. Repositórios, terminais e o banco de dados de coordenação permanecem no host do operador. Em troca, o operador assume instalação, autenticação de provedores, backup, correção, dimensionamento de recursos e proteção de acesso remoto.

## Comparado a contêineres ou sandboxes de segurança

O ThreadCells não é nenhum deles. Worktrees gerenciados e políticas de autoridade reduzem erros de coordenação, mas não isolam processos de provedores nativos da conta do sistema operacional.

## Comparado a fábricas autônomas de software

O ThreadCells enfatiza delegação limitada, terminais inspecionáveis, resultados explícitos, decisões do proprietário e conclusão apoiada por evidências. Ele não promete que agentes possam entregar software arbitrário sem revisão.

O ThreadCells é um downstream independente do AWS Labs CLI Agent Orchestrator e mantém internals compatíveis de `cao` quando necessário. Não é um substituto imediato para produtos de agentes não relacionados, como OpenHands ou Hermes. Escolha-o para operações locais de CLI nativa e controle durável de supervisor/trabalhador, e não para multitenancy hospedada ou abstração ampla de plataforma.
