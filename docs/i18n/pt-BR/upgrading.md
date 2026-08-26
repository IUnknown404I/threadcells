---
slug: upgrading
source: docs/UPGRADING.md
source_sha256: sha256:2416693b9c7d885ea50720f796e2f729749b22a9f7002e3b8206473dfddcc4a2
---
# Atualizando o ThreadCells

Uma atualização é uma promoção controlada de candidato com rollback verificado, não uma sobrescrita no local de quaisquer arquivos que estejam em execução.

## Antes da atualização

- Leia as notas da release e [Limitações](LIMITATIONS.md).
- Confirme a integridade atual e as identidades de build ativa/de rollback.
- Deixe que operações críticas de provedores/pesadas alcancem um limite seguro.
- Inspecione workflows abertos e resultados entregues.
- Crie um backup consistente e execute verificações de integridade do banco de dados.
- Preserve o candidato atual para rollback.

## Build e verificação

A partir do commit-fonte pretendido:

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.0a2-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

Não promova se a identidade do candidato diferir do commit revisado ou se as verificações de docs/Web/build falharem.

## Staging e promoção

Use as ferramentas canônicas de deployment local para fazer o staging do candidato sem alterar o ponteiro ativo. Verifique os arquivos em staging e promova atomicamente; então reinicie apenas os serviços ThreadCells que consomem a release.

Resultado esperado: Settings → About, o rodapé de Docs e os metadados de release identificam a mesma revisão do candidato.

## Verificações após a atualização

1. `curl -fsS http://127.0.0.1:9889/health`
2. Abra Home e inspecione o status de capacidade/disco.
3. Abra os Agents/Flows existentes e confirme que as relações duráveis permanecem.
4. Compare a prontidão dos provedores em Settings e Spawn.
5. Confirme que a autorização do operador está configurada e que as mutações protegidas permanecem bloqueadas até o desbloqueio.
6. Abra Statistics e confirme que uma atualização/reinicialização não duplica o uso.
7. Abra as rotas de Docs e verifique a identidade de build empacotada.
8. Verifique o streaming/reconexão do terminal.
9. Verifique se o manifesto do PWA e o service worker não armazenam em cache solicitações dinâmicas.
10. Abra Settings → Telegram e confirme seu estado seguro de configuração; se as credenciais nativas já estiverem configuradas, execute as verificações explícitas de conexão e mensagem de teste.
11. Para um agente aberto que atravesse a promoção, confirme que qualquer reinicialização da conexão de controle seja concluída uma vez e que o mesmo workflow durável continue sem um despertar do proprietário ou filho/efeito duplicado.

## Reparos históricos

Uma atualização pode incluir um reparo de dados delimitado. Execute-o somente quando a evidência da fonte for determinística, mantenha-o idempotente e registre as contagens antes/depois. A telemetria ausente de provedores deve continuar ausente; nunca invente uso histórico.

## Rollback

Se a aceitação falhar materialmente:

1. preserve o candidato com falha e os logs seguros relevantes;
2. mude o ponteiro ativo canônico para o candidato de rollback verificado;
3. reinicie apenas os serviços necessários;
4. verifique o build de rollback e as superfícies principais;
5. restaure o banco de dados anterior à atualização somente se a compatibilidade de esquema/dados exigir isso.

Não use reset Git destrutivo nem exclua evidências mais recentes do runtime para simular um rollback.

Um Full Cleanup explicitamente confirmado é a exceção à retenção normal de releases locais: ele remove todas as releases inativas comprovadas, inclusive o rollback selecionado durante a implantação, e deixa somente a release ativa e imutável. Não o execute durante a aceitação da atualização nem enquanto algum agente estiver executando. Após um Full Cleanup bem-sucedido, restaure a disponibilidade de rollback somente preparando outra release verificada e imutável; nunca a reconstrua a partir de um diretório não verificado.

Veja [Deployment local](DEPLOYMENT.md) e [Backup e restauração](BACKUP_AND_RESTORE.md).
