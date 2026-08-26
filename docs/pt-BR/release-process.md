---
slug: release-process
source: docs/RELEASE_PROCESS.md
source_sha256: sha256:82961559c0645676fcec5126113c53f17492ccd1b7c0c7100552689b770353ae
---

# Processo de lançamento

Crie um candidato local isolado a partir de uma árvore limpa já commitada com `scripts/build_local_candidate.py --output <new-directory>`. Ele empacota a Docs/UI gerada e um wheel local. Verifique `SHA256SUMS`, inspecione `candidate-manifest.json`, `sbom.cdx.json` e `EVIDENCE.md`, e então execute a instalação limpa documentada usando um novo prefixo. Publicar uma tag, branch remota, pacote, imagem ou lançamento público nunca é uma ação comum de implementação.

## Checklist de lançamento

1. Conclua a implementação e uma revisão integrada independente.
2. Execute testes focados mais um contorno significativo de build/navegador de produção.
3. Execute `git diff --check` e a auditoria de superfície pública.
4. Faça commit da árvore exata aceita.
5. Gere o candidato a partir desse commit, nunca de um worktree com alterações não commitadas.
6. Verifique manifesto, checksums, SBOM, identidade do build, rotas da Docs e instalação limpa.
7. Preserve o runtime anterior e um backup do banco de dados antes da promoção local.
8. Trate qualquer push público, tag, pacote, imagem ou lançamento como uma ação separada aprovada pelo proprietário.

As evidências de lançamento comprovam o que foi testado e empacotado; elas não aprovam por si mesmas a publicação nem certificam cada propriedade de licença/segurança das dependências.

## Distribuição de lançamento OCI

Os lançamentos alpha publicados aprovados também têm um artefato público de distribuição OCI em `ghcr.io/iunknown404i/threadcells-release-bundle`. Ele contém o arquivo de lançamento verificado, wheel Python, inventários de checksum, manifesto do candidato, SBOM e metadados do pacote de lançamento para uma tag de lançamento e revisão de origem exatas.

Esse pacote é um bundle de distribuição, não uma imagem Docker nem um ambiente de implantação em contêiner compatível. Use o processo normal de instalação e implantação do candidato depois de verificar seus checksums; não tente executar o artefato OCI como um serviço ThreadCells.

`.github/workflows/publish-release-bundle.yml` publica em um GitHub Release aprovado ou por um dispatch explícito de preenchimento retroativo. Ele aceita somente tags anotadas `v0.X.Y-alpha.N` com uma prerelease não rascunho existente, recompila e verifica a origem exata da tag, recusa substituir uma tag de versão incompatível e atualiza apenas `latest-alpha`. O ThreadCells não publica uma tag `latest` sem qualificação durante a prévia técnica.

## Convenção de linhas de versão

O ThreadCells segue a ordenação normal de prereleases do SemVer. Durante a prévia alpha, `0.1.X` identifica uma iteração significativa de produto, confiabilidade ou documentação; `alpha.N` identifica publicações adicionais dentro dessa mesma iteração quando elas são realmente necessárias.

- `v0.1.0-alpha.1` foi o primeiro alpha público.
- `v0.1.0-alpha.2` é uma prévia técnica publicada e imutável.
- `v0.2.0-alpha.1` é a linha de lançamento consolidada de multilíngue e confiabilidade.
- `v0.3.0-alpha.1` adiciona consistência de ciclo de vida, ordem de criação durável, Full Cleanup e política sistêmica de roteamento.
- `v0.3.0-alpha.2` corrige a entrega do Workflow Composer e torna a saída do terminal definitiva para a autoridade de workflow executável.
- `v0.3.0-alpha.3` adiciona localização em inglês e russo à interface autenticada e um único corpus canônico de Docs por idioma para o aplicativo e o site público.
- Uma publicação posterior na mesma linha de lançamento incrementa apenas a sequência alpha; um novo contorno de produto incrementa deliberadamente a versão semântica.

Nunca mova uma tag existente. Mudanças apenas de governança do repositório não acionam aumento de versão nem lançamento. Atualize todas as superfícies canônicas que carregam versão juntas somente quando o próximo contorno significativo de implementação estiver pronto para publicação.
