---
slug: statistics
source: docs/STATISTICS.md
source_sha256: sha256:ca9ce387ff845fb61aba3bc22c45084a825764e2ce7e156d6963e152e490de2b
---

# Estatísticas e uso do provedor

Statistics resume o uso que as CLIs de provedores compatíveis realmente emitem. Ele ajuda a responder quais sessões, perfis, projetos e provedores consumiram tokens de modelo; não é um livro-razão de cobrança e não inventa valores ausentes.

## O que os números significam

Para Codex, o ThreadCells registra os contadores cumulativos nativos do provedor disponíveis na telemetria de rollout:

- tokens de entrada;
- tokens de entrada em cache;
- tokens de saída;
- tokens de raciocínio;
- total de tokens.

A entrada em cache continua visível separadamente. Ela não é somada silenciosamente outra vez como entrada nova. Uma métrica que o provedor não informou aparece como **Não informado**, e não como um zero enganoso.

As tabelas padrão omitem tokens de gravação de cache porque nenhum adaptador atual expõe isso como métrica compatível significativa. A API normalizada mantém um campo opcional de compatibilidade para que um adaptador futuro possa adicionar suporte verdadeiro sem uma migração de banco de dados.

Informações sobre crédito, preço e custo do provedor são mostradas somente quando o adaptador fornece um valor compatível e autorizado. O ThreadCells não estima faturas a partir de totais de tokens.

## Quando o uso aparece

O uso é coletado enquanto uma sessão ao vivo é executada e armazenado de forma durável. Uma sessão não precisa ser excluída, retirada ou limpa antes de contribuir para Statistics. Sessões concluídas, mas retidas, continuam contando.

Codex emite snapshots cumulativos. O ThreadCells cria checkpoints desses snapshots e atualiza o mesmo registro canônico de uso, portanto polling, reinicialização, repetição ou retomada não contam os mesmos tokens duas vezes.

## Lendo a página

Comece pelos totais globais e use as tabelas de dimensão para localizar o uso por terminal, sessão, projeto, provedor ou perfil. Os totais usam os mesmos registros normalizados das visualizações detalhadas.

Um exemplo de investigação:

1. Observe uma alta nos tokens globais de saída.
2. Abra a dimensão da sessão para identificar a sessão responsável.
3. Compare seu projeto, provedor e perfil.
4. Abra Agents para inspecionar o terminal correspondente e o resultado durável.

## Dados históricos

Atualizações podem recuperar uso histórico somente quando a evidência nativa do provedor retida pode ser associada deterministicamente a uma sessão do ThreadCells. Dados de origem ambíguos ou ausentes permanecem desconhecidos. Um reparo é idempotente: executá-lo novamente não deve criar um registro duplicado.

A análise legada de texto de terminal por melhor esforço pode permanecer em bancos de dados antigos para proveniência. Quando existe um registro nativo exato do provedor, o registro exato substitui a aproximação legada nos totais visíveis.

## Solução de problemas

- **Uma sessão ao vivo está ausente:** atualize a página, verifique se o provedor suporta coleta de uso e confirme que o rollout do provedor continua legível pela conta de serviço.
- **Um campo diz Não informado:** o provedor não forneceu essa métrica. Não a interprete como zero.
- **Os totais parecem duplicados após reiniciar:** compare as dimensões de sessão e terminal e mantenha o banco de dados para diagnóstico; a repetição deve atualizar um checkpoint, não inserir um segundo total cumulativo.
- **A cobrança é diferente:** use o sistema de cobrança do próprio provedor como autoridade de cobrança. O ThreadCells informa telemetria operacional.

Para capacidade — não contabilização de tokens — consulte [Modelo de capacidade e recursos](RESOURCE_MODEL.md).
