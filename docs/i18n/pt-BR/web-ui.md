---
slug: web-ui
source: docs/WEB_UI.md
source_sha256: sha256:d3556d3674af5593090f679a3897b8b3a3bfe79b540c9e97ab4ffdf5f05e76d7
---

# Usando a Web UI

A Web UI é a visão ao vivo do operador sobre o ThreadCells. Ela foi projetada para um listener de loopback e funciona normalmente em um navegador ou como uma PWA básica instalada. Instalá-la não adiciona comportamento operacional offline nem um novo limite de autenticação.

![Página inicial ao vivo do ThreadCells com resumos densos de sessões, agentes e fluxos de trabalho](/media/screenshots/threadcells-home.webp)

## Áreas principais

- **Home** resume o histórico durável de sessões e agentes, a atividade atual, a atenção do proprietário e as contagens de status First/Last/Total sem carregar todos os terminais.
- **Agents** oferece as visualizações Sessions, Statuses e Profiles para terminais, identidade de perfil/provedor, estado de execução, estado de fluxo de trabalho e resultados duráveis.
- **Flows** cria, habilita, desabilita, inspeciona e executa manualmente agendamentos recorrentes de agentes. Os agentes resultantes e o ciclo de vida do fluxo de trabalho aparecem em Agents.
- **Statistics** exibe o uso informado pelo provedor, sem métricas inventadas.
- **Settings** contém General, Orchestration Capacity, Profiles, Providers, Housekeeping, notificações globais de instalação do Telegram e About.
- **Docs** fornece a documentação pública permitida empacotada com a compilação em execução.
- **Spawn Agent** inicia uma nova sessão a partir de um projeto, provedor e perfil.
- **Add Agent** inicia outro terminal dentro da vida útil exata da sessão selecionada; ele não entra em outra sessão histórica que, por acaso, tenha o mesmo nome.

URLs diretas são compatíveis. O histórico do navegador deve preservar a página selecionada de Settings e Docs.

## Um ciclo operacional normal

1. Verifique Home para a atividade atual de sessão/fluxo de trabalho e Settings para a saúde do host, pressão de disco e capacidade disponível.
2. Use Spawn Agent e confirme que o provedor selecionado está pronto.
3. Acompanhe a nova sessão em Agents.
4. Use Flows para agendamentos recorrentes. Acompanhe, em Agents, os agentes que eles iniciam.
5. Leia e incorpore resultados duráveis antes de aposentar filhos.
6. Use Statistics para entender o uso informado pelo provedor.

Os rótulos de status vêm da verdade durável do plano de controle. **Processing** significa que um turno está ativo; **Ready** significa que o runtime do provedor está vivo e realmente ocioso. Rótulos de fila distinguem esgotamento de capacidade do provedor, barreiras de aposentadoria de filhos e continuação geral do fluxo de trabalho. Um selo de controle do proprietário permanece categórico, enquanto o painel expandido Owner Decision mostra o motivo durável concreto.

Sessões ativas e históricas continuam sendo vidas úteis duráveis separadas. Excluir uma sessão histórica remove somente aquela vida útil exata elegível. Excluir um terminal encerrado também verifica sua identidade exata de runtime, lease de escritor, proteção de fluxo de trabalho/resultado e relação de sessão antes da limpeza; estados ambíguos ou ativos permanecem protegidos.

![Visualização de status ao vivo de Agents com caminhos locais de worktree removidos da captura pública](/media/screenshots/threadcells-agents.webp)

## Configurações protegidas

Mutações sensíveis compartilham um controle **Unlock operator changes**. Os estados ausente, inválido, bloqueado, desbloqueado e expirado são distintos. O tamanho mínimo exato do segredo é cinco caracteres, e a sessão autenticada padrão dura cinco minutos.

A UI envia o segredo somente para desbloquear, limpa-o imediatamente e nunca o coloca em persistência do navegador nem em exportações. Capacidade, alterações privilegiadas de perfil/provedor, configuração/testes do Telegram, execução de Housekeeping e inícios de proprietário aplicáveis permanecem bloqueados sem a sessão do servidor.

Siga [Autorização do operador](OPERATOR_AUTHORIZATION.md) para provisionar o verificador com segurança.

## Seleção de provedor e perfil

Rótulos de provedor distinguem **Built-in adapter** de **CLI ready**, **CLI not installed**, **Authentication required**, **Installed but unhealthy** ou **Readiness unverified**. Spawn desabilita somente um provedor cuja indisponibilidade tenha sido comprovada e usa a mesma verificação prévia do servidor que Settings.

Perfis priorizam descoberta integrada/personalizada pesquisável e prévias resolvidas. A importação/exportação de artefatos brutos fica intencionalmente em Advanced. Selecionar o perfil excepcional de proprietário XHigh exibe um aviso de autoridade e exige seu caminho de concessão separado.

## Notificações do Telegram

Settings → Telegram configura um único destino global da instalação, independente de projetos. O token de bot é somente de escrita na UI; ações de conexão e de mensagem de teste são explícitas, e a ação separada de limpeza confirmada desabilita a entrega enquanto remove a credencial. A entrega habilitada cobre apenas conclusão de nível superior, controles que exigem atenção do proprietário e falha inesperada de terminal de nível superior, com supressão durável de duplicatas e entrega fail-open. Consulte [Notificações do Telegram](TELEGRAM_NOTIFICATIONS.md).

## Statistics

Statistics inclui sessões ativas, concluídas e retidas não excluídas assim que a telemetria durável do provedor fica disponível. Entrada em cache e saída de raciocínio permanecem separadas; campos indisponíveis mostram **Not reported**. Consulte [Estatísticas e uso do provedor](STATISTICS.md).

## Leitor de Docs

A navegação de Docs é agrupada pela jornada de aprendizagem, pesquisável e acompanhada por uma estrutura na página em telas largas. Links de anterior/próximo seguem a ordem publicada do manifest. O leitor expõe somente Markdown empacotado e permitido; ele não possui navegador arbitrário de sistema de arquivos nem endpoint de edição.

## Full Output

Full Output renderiza texto de provedor retido para inspeção humana depois de remover sequências de controle ANSI/VT e manipulação do cursor do terminal. A sanitização impede que controles de apresentação reescrevam o histórico visível; ela não reinterpreta, executa nem certifica o texto do provedor.

## Instalar como aplicativo

Navegadores Chromium compatíveis podem instalar o ThreadCells pela ação de instalação do navegador. O manifest usa a marca ThreadCells e abre em modo de exibição independente. No iOS, é possível usar **Add to Home Screen**.

Quando o acesso do operador é protegido por credenciais do navegador, o manifest e as solicitações relacionadas de mesma origem usam o mesmo limite de credenciais. O acesso entre origens continua limitado a origens explicitamente confiáveis; os metadados da PWA não contornam os controles de operador nem de acesso remoto.

O service worker conservador armazena em cache apenas recursos estáticos imutáveis com fingerprint. Ele nunca armazena em cache navegação HTML, APIs, autorização do operador, agentes, sessões, fluxos de trabalho, resultados, Statistics, terminais, WebSockets ou mutações. Se o servidor não estiver disponível, o aplicativo instalado relata a falha real de rede em vez de apresentar estado operacional obsoleto.

Uma nova compilação imutável substitui recursos antigos com fingerprint pelo ciclo normal de atualização do service worker do navegador. O ThreadCells não mantém o operador em uma shell offline obsoleta.

## Uso responsivo e com teclado

A navegação principal, Docs, Settings, tabelas e controles de terminal oferecem suporte a larguras de telefone, tablet e desktop. Tabelas operacionais largas rolam horizontalmente em telas estreitas, em vez de reduzir valores até ficarem ilegíveis.

Em telefones, cada cabeçalho de sessão na Home usa uma linha dedicada ao nome e outra linha separada para metadados/ações. Os cartões de agentes sempre usam a lista canônica de uma coluna; o seletor List/Grid fica oculto. Layouts de tablet e desktop preservam a escolha List/Grid.

Use a navegação normal Tab/Shift-Tab e indicadores de foco visíveis. Blocos de código em Docs rolam horizontalmente e oferecem um controle de cópia. O comportamento de teclado do terminal continua nativo do provedor; rolagem por toque não deve injetar entrada no terminal.

## Limite de acesso

A UI e os Docs comuns não fornecem login geral de usuário. Mantenha o ThreadCells em loopback. Use um túnel SSH ou um proxy Caddy/Authelia autenticado de [Acesso remoto](REMOTE_ACCESS.md); nunca publique diretamente a porta 9889.
