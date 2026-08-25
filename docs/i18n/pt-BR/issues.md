---
slug: issues
source: docs/ISSUES.md
source_sha256: sha256:fa980f53f7ec42635a41273a8d82bdf2da52cab760ee5da675fbc6a00792cee4
---

# Política de Issues públicas

As GitHub Issues são o backlog público curado do ThreadCells, não uma transcrição de avisos, auditorias ou depuração de lançamentos.

## Elegibilidade

Uma Issue pública normalmente deve satisfazer todas estas condições:

- o problema ou oportunidade continua sem solução;
- é reproduzível ou sustentado por evidências duráveis;
- tem impacto significativo para usuário, projeto, confiabilidade, documentação ou manutenção;
- o acompanhamento público é útil e acionável para o projeto ou a comunidade;
- a divulgação pública é segura;
- o comportamento ou resultado esperado é claro; e
- critérios concretos de aceitação podem ser declarados.

Evidências técnicas duráveis podem substituir etapas de reprodução quando a reprodução determinística for impraticável.

Perguntas, solução de problemas e conversa aberta pertencem a [Discussions Q&A](https://github.com/IUnknown404I/threadcells/discussions/categories/q-a). Explore propostas iniciais centradas no problema e no caso de uso em [Discussions Ideas](https://github.com/IUnknown404I/threadcells/discussions/categories/ideas). Mova um achado ou proposta para Issues somente depois que se tornar confirmado, concreto, seguro para o público e acionável sob esta política.

## O que não pertence a Issues públicas

Não crie uma Issue pública apenas para:

- administração exclusiva do proprietário do repositório ou da conta;
- administração de credenciais ou trabalho de infraestrutura privada;
- credenciais, segredos ou detalhes de segurança que não são seguros de divulgar;
- ruído transitório de CI, ambiente, rede ou runner;
- achados já resolvidos;
- identificadores isolados de runtime sem uma classe subjacente de problema reproduzível;
- avisos que se comportam com segurança sem defeito demonstrado;
- aprimoramento especulativo sem problema e resultado definidos;
- observações temporárias de lançamento ou depuração; ou
- notas não classificadas de uma auditoria ou varredura de dívida residual.

Ações exclusivas do proprietário pertencem ao canal operacional do proprietário do repositório, não ao backlog de contribuidores. Um achado torna-se uma Issue pública somente depois de passar pelo gate de elegibilidade.

## Conteúdo do relato

Use o formulário de Issue correspondente e forneça as partes úteis desta estrutura:

1. **Problema / Contexto**
2. **Impacto**
3. **Comportamento atual**
4. **Comportamento esperado**
5. **Reprodução ou Evidência**
6. **Critérios de aceitação**
7. **Não objetivos**, quando útil

Inclua informações de ambiente ou versão apenas quando afetarem o relato. Oculte logs e capturas de tela. Nunca inclua segredos, credenciais, dados pessoais, mensagens privadas, caminhos privados desnecessários, bancos de dados de estado ou transcrições de terminal.

Vulnerabilidades e achados sensíveis à segurança devem usar a rota privada em [SECURITY.md](../SECURITY.md), não uma Issue pública.

## Triagem e duplicatas

Pesquise Issues abertas e fechadas antes de registrar. Mantenedores vinculam duplicatas à Issue canônica e as fecham como duplicadas em vez de dividir discussão e evidências.

Use o menor conjunto útil de rótulos. `bug`, `enhancement`, `documentation`, `accessibility` e `technical-debt` descrevem o trabalho; `duplicate` descreve a triagem. Mantenedores podem solicitar evidências ausentes antes de decidir se um relato se qualifica.

Feche uma Issue quando os critérios de aceitação forem satisfeitos, quando ela duplicar uma Issue canônica ou como não planejada com uma razão concisa quando estiver fora do escopo, não puder se tornar acionável ou não justificar mais o acompanhamento do projeto. Relatos já resolvidos devem apontar para as evidências de resolução.

## Rótulos para contribuidores

Use `good first issue` somente para trabalho seguro, limitado e de baixa ambiguidade, com contexto e critérios de aceitação suficientes para um novo contribuidor. Use `help wanted` somente quando a contribuição externa for realmente bem-vinda e a tarefa estiver suficientemente especificada.

Limites críticos de segurança ou autenticação, comportamento de ciclo de vida e exatamente uma vez, segurança destrutiva, autoridade de lançamento, confiança do provedor ou limites de execução remota de código, migrações e integridade de dados nunca são automaticamente trabalho para iniciantes.
