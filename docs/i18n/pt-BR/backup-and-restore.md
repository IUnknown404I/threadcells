---
slug: backup-and-restore
source: docs/BACKUP_AND_RESTORE.md
source_sha256: sha256:d5f0fb66a4513e8c56a811e1e21c5384f39831b8729af46e22b67896a79e3a66
---
# Backup e restauração

Um backup útil do ThreadCells preserva o estado durável de coordenação e a configuração necessária para interpretá-lo. O código instalado e os caches de build normalmente podem ser recriados; o banco de dados, a configuração administrada pelo operador e as evidências nativas dos provedores podem não poder.

## O que importa

Faça backup, conforme aplicável, de:

- o banco de dados SQLite do ThreadCells e seus arquivos SQLite associados;
- a configuração e o ambiente do serviço, excluindo segredos em texto simples de arquivos ad hoc;
- o arquivo de verificador do operador como um artefato separado, protegido e adjacente a segredos;
- o arquivo de token do bot do Telegram, se configurado, como uma credencial criptografada separadamente, com proprietário e modo preservados;
- o contexto dos agentes, anexos e logs exigidos pela política de retenção;
- os metadados de worktrees gerenciados e de releases necessários para interpretar o trabalho ativo;
- os manifestos e as identidades exatos, ativos e candidatos a rollback;
- o estado de provedores externos apenas de acordo com a política de backup suportada pelo próprio provedor.

Os repositórios Git já devem ter sua própria estratégia de backup/remoto. Um backup do banco de dados do ThreadCells não substitui a preservação dos commits.

## O que pode ser recriado

Dependências Web baixadas, revisões de navegador, caches de pacotes, diretórios temporários de build e conteúdos de candidatos verificados normalmente podem ser recriados a partir do código-fonte e dos lockfiles. Não aumente todos os backups com caches apenas porque eles existem em caminhos de runtime.

## Sequência consistente de backup

1. Registre a identidade da fonte/candidato ativo e o estado atual do serviço.
2. Evite iniciar novas sessões ou mutações durante a janela do snapshot.
3. Use o mecanismo canônico de backup do banco de dados em vez de copiar cegamente um arquivo SQLite em uso.
4. Execute a verificação de integridade do SQLite no backup.
5. Copie a configuração necessária, o verificador e os artefatos de token do Telegram configurados com as permissões preservadas e sem imprimir seu conteúdo.
6. Registre checksums e armazene o arquivo fora da raiz de estado em uso.
7. Teste se o backup pode ser listado e lido pelo principal de recuperação pretendido.

Se a ferramenta de deployment fornecer um comando de backup, use-o: ela entende o caminho real do banco de dados e a coordenação do serviço. Nunca coloque segredos de provedores ou do operador em texto simples no histórico do shell para criar um arquivo.

## Verificação

No mínimo, verifique o banco de dados SQLite copiado:

```bash
sqlite3 /path/to/backup.db 'PRAGMA integrity_check;'
```

Resultado esperado: `ok`. Registre também um checksum e confirme que o arquivo contém a configuração, o verificador e a identidade de build esperados sem expor seu conteúdo nos logs.

Um backup não testado é apenas uma hipótese. Periodicamente, ensaie a restauração em um caminho isolado e em uma porta somente local.

## Ordem de restauração

1. Pare ou isole o serviço ThreadCells de destino.
2. Preserve o estado atual com falha para rollback forense.
3. Instale ou selecione o candidato compatível exato.
4. Restaure o banco de dados e o estado mutável com a propriedade esperada pela conta de runtime.
5. Restaure a configuração do serviço.
6. Restaure o verificador do operador com um proprietário confiável distinto, modo legível pelo serviço e uma cadeia de diretórios pai confiável; restaure um token do Telegram aplicável em `$CAO_HOME_DIR/secrets/telegram-bot-token` como um arquivo regular pertencente ao runtime com modo `0600`.
7. Execute verificações de integridade antes da inicialização.
8. Inicie no loopback e verifique a integridade/identidade de build.
9. Inspecione workflows ativos, resultados, terminais, projetos, preflight de provedores e Statistics antes de repetir o trabalho.

Não restaure apenas o banco de dados deixando código incompatível ou ambiente de serviço desatualizado. Não suponha que os processos do tmux e do provedor tenham sobrevivido de forma consistente; reconcilie cada processo ativo com o estado durável da sessão.

## Validação da recuperação

Após a restauração, verifique:

- Settings → About corresponde ao candidato pretendido;
- `/health` é bem-sucedido;
- projetos e histórico de sessões estão presentes;
- resultados entregues permanecem atribuíveis;
- a disponibilidade dos provedores reflete a instalação real do usuário de runtime restaurado;
- a autorização do operador informa que está configurada e desbloqueia com o segredo existente;
- o Telegram informa o estado de configuração segura esperado e, se restaurado, passa em verificações explícitas de conexão e mensagem de teste antes da ativação;
- os totais de Statistics são reproduzidos sem duplicação;
- releases ativos/de rollback permanecem corretamente identificados.

Backups são protegidos do Housekeeping automático. Aplique uma política de retenção separada e revisada ao armazenamento de backups.

O Full Cleanup não substitui a retenção de backups. Ele protege o banco de dados canônico e qualquer backup cuja disponibilidade para exclusão não esteja comprovada, mas remove intencionalmente cada release local inativa e de rollback representada por metadados confiáveis de release. Antes de autorizá-lo, confirme que todo ponto de recuperação necessário existe fora do conjunto de releases locais e teve sua integridade verificada. Depois disso, o operador deve esperar que o rollback local fique indisponível até que outra release verificada seja preparada.
