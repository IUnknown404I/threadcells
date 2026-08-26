---
slug: localization
source: docs/LOCALIZATION.md
source_sha256: sha256:fd2af656f4ff016e13870a9c45ac0bd0bfc1111964f464ee31b05fc538c57c98
---

# Guia de localização

O inglês é a autoridade canônica para a documentação pública do ThreadCells, o README raiz e as afirmações sobre o produto. Uma tradução pode melhorar a formulação natural, mas não pode omitir nem inventar comportamento, enfraquecer um limite de segurança, alterar um limite ou modificar um comando.

## Modelo de locale

Os locales de lançamento são `en`, `ru`, `zh-CN`, `es`, `pt-BR`, `de` e `ja`. O Markdown canônico em inglês permanece na origem nomeada por `docs/DOCS_MANIFEST.json`; os documentos mantidos na raiz por política conservam seus caminhos estabelecidos. Cada documento não inglês fica em `docs/LOCALE/SLUG.md` e registra:

```yaml
---
slug: overview
source: docs/OVERVIEW.md
source_sha256: sha256:EXACT_ENGLISH_SOURCE_HASH
---
```

Slugs, ordem do manifesto e participação na navegação são compartilhados entre os locales. Não crie um segundo manifesto ou renderizador de Docs específico para locale.

## Atualize uma tradução

1. Primeiro atualize e aceite o documento canônico em inglês.
2. Traduza todas as afirmações e títulos sem alterar código ou identificadores.
3. Atualize `source_sha256` a partir dos bytes exatos da origem canônica.
4. Execute `python3 scripts/validate_localizations.py`.
5. Gere o site e inspecione as rotas afetadas nas larguras desktop, tablet e mobile.

O validador rejeita slugs traduzidos ausentes, desatualizados, desconhecidos, duplicados ou incompatíveis. Um locale compatível não pode publicar silenciosamente uma tradução antiga depois que o inglês muda.

## Adicione um locale

Adicione o locale uma vez em `website/lib/locales.ts`, forneça seus metadados completos de landing/UI, adicione um documento traduzido para cada slug do manifesto, acrescente seu README localizado e estenda as verificações determinísticas de rota/navegador. Preserve o mesmo slug público ao trocar de idioma.

Adicionar um locale futuro como `fr` ou `ko` deve ser uma mudança de conteúdo limitada. Isso não deve exigir outra arquitetura de aplicação, manifesto ou Docs.

## Texto técnico

Mantenha estes elementos exatos, a menos que a origem canônica em inglês os altere:

- blocos de código cercados e comandos de shell;
- identificadores de código inline;
- caminhos de API, chaves de configuração, variáveis de ambiente, códigos de motivo, IDs de perfil/provedor, nomes de pacotes e caminhos de arquivo;
- nomes de produtos e provedores como ThreadCells, Codex, Claude Code, Git, Git worktree e tmux;
- destinos de links Markdown e caminhos de mídia.

Traduza naturalmente as explicações ao redor desses valores. Evite decalques literais que dificultem a leitura de orientações para desenvolvedores.

## Arquivos README

`README.md` é o inglês canônico. Cada README localizado segue a mesma estrutura de seções, aponta para as mesmas evidências e começa com o seletor compacto de sete idiomas. Destaque o idioma atual em negrito e use links relativos ao repositório para os outros seis.

## Aceitação visual

As traduções não precisam ter quebras de linha ou alturas de seção idênticas. Elas devem preservar hierarquia, tipografia legível, CTAs funcionais, mídia, tabelas, blocos de código, comportamento de cabeçalho/rodapé e ausência de overflow horizontal. Dê atenção especial à expansão do alemão, quebra de linhas em russo, navegação em espanhol e português e quebra de linhas em chinês/japonês.

A revisão semântica por um leitor fluente voltado a desenvolvedores continua obrigatória. Passar nas verificações de Markdown, hash, rota e navegador comprova frescor estrutural; não comprova a qualidade da tradução.
