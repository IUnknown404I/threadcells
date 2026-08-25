---
slug: security-model
source: docs/SECURITY_MODEL.md
source_sha256: sha256:6305e6199bae4706af6ed41e99eb0465ed0877bff4e83a7b1df57019f1a3383c
---

# Modelo de segurança

O ThreadCells destina-se a um único host Linux confiável operado por uma pessoa ou uma pequena equipe de confiança. Ele coordena poderosos agentes de programação nativos; não é um sandbox para usuários, prompts, repositórios ou plugins de provedores hostis.

## Limites práticos de confiança

### Host e usuário de runtime

Qualquer coisa que possa ser lida ou executada pela conta do sistema operacional do ThreadCells pode ficar acessível a um agente nativo. Use uma conta dedicada, permissões mínimas de sistema de arquivos, instalações de provedores revisadas e repositórios adequados para automação.

Os worktrees gerenciados separam escritores, mas não os contêm. Não forneça à conta de runtime credenciais nem acesso ao host de que um agente não precisa.

### Acesso pela Web

A UI comum e a Docs empacotada não implementam login geral de usuários. Faça bind em `127.0.0.1`. Use um túnel SSH para acesso ocasional ou Caddy mais Authelia para uma URL HTTPS autenticada. Nunca exponha publicamente a porta bruta do ThreadCells.

Uma PWA instalada mantém o mesmo limite de confiança de rede. Seu service worker não pode fornecer estado operacional offline e não armazena em cache APIs, terminais, autorização, fluxos de trabalho, resultados ou Statistics.

### Autorização do operador

Mutações sensíveis do plano de controle usam um verificador de operador separado, provisionado por um principal distinto do sistema operacional. O comprimento mínimo exato do segredo é cinco caracteres; valores aleatórios mais longos são recomendados.

O ThreadCells armazena um verificador scrypt com salt, digests de sessão/concessão de curta duração, escopo, emissor, expiração, consumo e registros de auditoria — não o texto simples. O verificador e cada diretório pai não podem ser substituíveis pela conta de serviço. A sessão de navegador de cinco minutos usa um cookie HttpOnly, SameSite=Strict.

Esse limite protege as mutações configuradas. Ele não transforma toda a Web UI em uma aplicação autenticada multiusuário.

### Inicialização excepcional do proprietário

Inicializações owner-executor/XHigh exigem uma capability de uso único vinculada à revisão imutável exata do perfil, à revisão da configuração do provedor, ao projeto/worktree, à solicitação de sessão, à topologia, ao emissor e à profundidade de delegação. Ela é consumida atomicamente com os metadados do terminal.

Os fluxos Web Create Session e Add Agent para o builtin `critical_sol_xhigh_owner` exigem o mesmo aviso excepcional, confirmação explícita, desbloqueio do operador e capability de uso único com escopo. Add Agent vincula a concessão à sessão existente e ao worktree canônico resolvido, em vez de aceitar um caminho inserido pelo usuário. O caminho local `critical_sol_xhigh_owner --owner-xhigh` exige confirmação interativa explícita e inicialização somente em loopback. Nenhum desses caminhos concede autoridade a filhos ou a Web Settings não relacionados. O texto do prompt não pode criar nem delegar autoridade de proprietário.

### Provedores e artefatos importados

Adaptadores de provedores são pacotes executáveis confiáveis e exigem revisão do operador. JSON de provedor/perfil é entrada declarativa não confiável: caminhos executáveis, comandos, flags de shell, ambientes, segredos brutos, comandos MCP arbitrários e autoridade curinga não concedida são rejeitados.

A autenticação do provedor permanece no próprio mecanismo compatível do provedor. Exportações de registro omitem valores secretos e concessões de uso único.

As credenciais de controle por terminal têm escopo para o processo de terminal/provedor. O ThreadCells inicia o servidor tmux de longa duração por meio de um bootstrap sem credenciais, de modo que sua linha de comando de processo persistente não retenha uma credencial de terminal. Essas credenciais continuam sensíveis para processos executados pela mesma conta de runtime confiável.

### Notificações do Telegram

A entrega pelo Telegram é opcional, desativada por padrão, global à instalação e independente da configuração do projeto. Seu token de bot é uma configuração Web somente para escrita, armazenada fora do SQLite na raiz privada de estado do ThreadCells como um arquivo regular `0600` de propriedade do runtime. APIs de leitura expõem apenas o estado seguro da configuração. A autorização do operador protege atualizações e ações explícitas de conexão/teste.

Mensagens de ciclo de vida usam resumos seguros fixos e omitem prompts, saída de terminal, corpos de exceções, caminhos e credenciais. A entrega externa falha de forma aberta para a execução de fluxos de trabalho e é desduplicada de maneira durável; habilitar notificações não reproduz eventos observados enquanto estavam desativadas.

## Sensibilidade dos dados

Trate o banco de dados SQLite, logs de terminal, prompts, resultados, anexos, worktrees gerenciados, backups, verificador do operador e histórico de rollout nativo do provedor como sensíveis. Eles podem conter código proprietário ou conteúdo fornecido pelo usuário, mesmo quando o próprio ThreadCells evita registrar credenciais.

Não coloque segredos do operador/provedor/Telegram em texto simples em repositórios, dumps de ambiente, bundles de suporte, telemetria, armazenamento do navegador, respostas de API ou capturas de tela.

## Operações destrutivas

O Housekeeping é orientado por plano e falha de forma fechada. Recursos desconhecidos, ilegíveis, abertos, ativos, referenciados, com identidade alterada ou metadados incompletos permanecem protegidos. Backups nunca são excluídos automaticamente.

A implantação preserva um runtime de rollback e um backup do banco de dados. Publicação, exposição à rede pública e mudanças destrutivas de histórico continuam sendo decisões separadas do proprietário.

## Responsabilidades do operador

- Atualize o SO, ThreadCells, CLIs dos provedores, proxy reverso e camada de autenticação.
- Revise prompts, perfis, adaptadores e repositórios antes de conceder acesso de escrita.
- Mantenha o usuário de runtime e o ambiente de serviço com privilégios mínimos.
- Faça backup e teste a restauração do estado durável.
- Inspecione diffs/resultados antes de merge, implantação ou publicação.
- Preserve controles de acesso por loopback ou proxy autenticado.
- Gire as credenciais dos provedores e substitua o verificador do operador por um processo administrativo seguro.

## Reportar um problema de segurança

Siga [SECURITY.md](../SECURITY.md). Não inclua credenciais ativas, estado privado ou detalhes públicos de exploração além do que os mantenedores precisam para uma reprodução segura.
