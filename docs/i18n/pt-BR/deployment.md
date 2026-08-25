---
slug: deployment
source: docs/DEPLOYMENT.md
source_sha256: sha256:0a1e81cb71e94e5fe3a400baf7d83caaca2b9abf0a13cc7c7fcd19e62761e835
---
# Deployment local

O deployment do ThreadCells promove um candidato imutável verificado para o runtime local. Isso não implica publicação, um push/tag Git, release de pacote ou exposição à rede pública.

## Disciplina de candidatos

Faça o build a partir de um único commit-fonte limpo e exato e verifique o candidato antes do staging:

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
python3 scripts/verify_local_candidate.py \
  --candidate "$PWD/threadcells-candidate/threadcells-0.2.0a1-local"
```

O candidato deve conter código Python, ativos Web empacotados, o bundle de Docs permitido, identidade de build, checksums e metadados de release da mesma revisão.

O staging no host usa um grupo dedicado de manutenção de releases para que o control plane em execução possa ler, mas não substituir, um candidato imutável, enquanto os serviços Housekeeping podem remover uma release explicitamente desprotegida. Crie esse grupo de sistema uma vez antes do primeiro staging no host:

```bash
sudo groupadd --system threadcells-release-admin
```

As unidades instaladas do control plane e do Housekeeping desabilitam escritas de bytecode Python. Isso impede que importações de rotina alterem a propriedade ou o conteúdo dentro de uma release imutável, inclusive enquanto o grupo de manutenção de releases com escopo restrito está ativo.

O comando de staging falha de forma fechada se esse grupo não estiver disponível. Ele mantém candidatos de release, o ponteiro ativo atômico, o lock de staging e os metadados de proteção de releases sob uma âncora `/var/lib/threadcells` pertencente ao root, fora do estado pertencente ao runtime. Os serviços de produção são executados por meio de `/var/lib/threadcells/active`, não por um link de comando gravável pelo runtime. Os caminhos de candidatos devem ser filhos diretos de `/var/lib/threadcells/releases`; destinos de links simbólicos e de lock/metadados alternativos são recusados.

## Sequência de promoção segura

1. Registre o runtime ativo atual e sua integridade.
2. Preserve-o como alvo de rollback verificado.
3. Crie e verifique a integridade de um backup do banco de dados.
4. Faça o staging do candidato verificado exato com o mecanismo canônico de deployment do repositório.
5. Verifique novamente o candidato em staging.
6. Promova a identidade em staging de forma atômica.
7. Reinicie apenas os serviços ThreadCells necessários.
8. Execute a aceitação em produção no loopback ou pelo caminho de acesso protegido existente.

Não sobrescreva o diretório ativo no local. Um ponteiro/link simbólico de release ou mecanismo canônico equivalente deve identificar sem ambiguidade os candidatos ativo, de rollback e em staging.

Depois que o staging tiver registrado o candidato exato, promova-o pela operação canônica com lock:

```bash
sudo python3 deployment/promote-ops-p1.py \
  --system-root / \
  --candidate-root /var/lib/threadcells/releases/RELEASE_ID \
  --expected-commit EXACT_PUBLIC_SHA
```

Use `--rollback-root` quando uma release de rollback canônica verificada já estiver presente. A operação é idempotente: uma nova tentativa conclui uma transição de ponteiro/metadados interrompida sem inventar uma nova identidade de release.

## Aceitação

Verifique ao menos:

- integridade e identidade de build em Settings → About;
- Home, Agents, Flows, Statistics, Settings, Docs e Spawn Agent;
- inventário de provedores e um preflight seguro;
- comportamento de autorização do operador configurado/bloqueado/desbloqueado/mutação protegida;
- estado global de configuração segura do Telegram e, somente quando as credenciais nativas já estiverem configuradas, comportamento explícito de conexão/teste;
- conexão e reconexão do terminal;
- continuidade de workflow/resultado;
- integridade do banco de dados e ausência de duplicação na reprodução de uso;
- registro do manifesto/ícones/service worker do PWA sem cache dinâmico.

## Rollback

O rollback troca para o candidato anterior preservado e reinicia apenas os serviços necessários. Restaure o banco de dados somente quando a nova versão tiver realizado uma migração incompatível ou danosa; uma restauração desnecessária do banco de dados pode descartar trabalho válido concluído após a promoção.

Após o rollback, verifique a identidade de build, a integridade, a compatibilidade de esquema, os workflows ativos e os terminais. Retenha o candidato com falha e os logs até que a causa raiz seja compreendida.

## Limites

A autoridade de deployment local não concede permissão para publicar pacotes, enviar a um remoto, criar uma tag/release ou expor uma porta bruta de serviço. Essas continuam sendo decisões separadas do proprietário.
