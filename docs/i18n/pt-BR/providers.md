---
slug: providers
source: docs/PROVIDERS.md
source_sha256: sha256:7f782daac9b50583042705af486afbdcc65d19ed545e0d8addd6e918808d7b0f
---

# Provedores

Um provedor é a CLI nativa do agente de programação que de fato executa o turno do modelo. O ThreadCells fornece um adaptador em torno dessa CLI para que inicializações, estado do terminal, cancelamento, relatório de capacidades e telemetria de uso disponível tenham uma forma comum.

## Três fatos diferentes

As telas de provedores separam deliberadamente três fatos fáceis de confundir:

| Fato | Significado |
| --- | --- |
| Adaptador integrado | Esta compilação do ThreadCells contém código de integração revisado para o provedor. |
| CLI instalada | O executável exigido está no `PATH` do usuário de runtime. |
| Pronto | A pré-verificação considera a CLI instalada compatível e autenticada, ou a CLI não consegue expor o estado de autenticação com segurança. |

Settings → Providers lista os adaptadores, inclusive aqueles cujo comando externo está ausente. Spawn Agent usa a mesma pré-verificação canônica e desabilita provedores comprovadamente indisponíveis.

Por exemplo, **Adaptador integrado · CLI não instalada** não é contraditório. Isso significa que o ThreadCells sabe operar o provedor, mas o host ainda não tem o programa desse provedor.

## Provedores integrados

A compilação atual registra estes adaptadores:

| Provedor | Comando canônico |
| --- | --- |
| Amazon Q Developer | `q` |
| Claude Code | `claude` |
| Codex | `codex` |
| Gemini CLI | `gemini` |
| GitHub Copilot CLI | `copilot` |
| Kimi CLI | `kimi` |
| Kiro CLI | `kiro-cli` |
| OpenCode CLI | `opencode` |

O registro é suporte factual do produto, não uma instrução para instalar todas as CLIs. Instale somente os provedores que pretende usar, seguindo as instruções oficiais e o fluxo de autenticação de cada provedor.

## Matriz de compatibilidade

Esta matriz descreve o contrato do adaptador nesta versão, não uma promessa de que toda versão de CLI externa ou conta está pronta em determinado host. **Compatível** significa que o adaptador implementa a capacidade diretamente, **Condicional** significa que o comportamento depende da CLI do provedor ou do modo de sessão, e **Não informado** significa que o ThreadCells não inventa os dados.

| Provedor | Iniciar/cancelar | Retomada e persistência | Conclusão estruturada | Telemetria de uso | Controles de modelo/raciocínio | Sonda de prontidão |
| --- | --- | --- | --- | --- | --- | --- |
| Codex | Compatível | Retomada condicional; persistência compatível | Condicional | Campos de token nativos do provedor compatíveis | Compatível | Comando, versão e autenticação |
| Claude Code | Compatível | Condicional | Condicional | Campos nativos do provedor condicionais | Seleção de modelo compatível; outros controles dependem do adaptador | Comando, versão e autenticação |
| Amazon Q Developer | Compatível | Condicional | Condicional | Não informado | Condicional | Comando e versão; autenticação não verificada |
| Gemini CLI | Compatível | Condicional | Condicional | Não informado | Condicional | Comando e versão; autenticação não verificada |
| GitHub Copilot CLI | Compatível | Condicional | Condicional | Não informado | Condicional | Comando e versão; autenticação não verificada |
| Kimi CLI | Compatível | Condicional | Condicional | Não informado | Condicional | Comando e versão; autenticação não verificada |
| Kiro CLI | Compatível | Condicional | Condicional | Não informado | Condicional | Comando e versão; autenticação não verificada |
| OpenCode CLI | Compatível | Condicional | Condicional | Não informado | Condicional | Comando e versão; autenticação não verificada |

Codex é o provedor de referência e de aceitação de releases. Outros adaptadores integrados continuam utilizáveis quando sua pré-verificação pública permite iniciar, mas o comportamento nativo do provedor e a autenticação podem variar. A visualização de capacidades ao vivo em Settings é a autoridade para uma compilação instalada.

## Rótulos de disponibilidade

O ThreadCells normaliza a pré-verificação em cinco estados voltados ao operador:

- **Pronto** (`INSTALLED_AND_READY`): instalado, compatível e autenticado quando a autenticação pode ser verificada.
- **Autenticação necessária** (`INSTALLED_NOT_AUTHENTICATED`): o comando existe, mas o provedor informa que é necessário fazer login.
- **Instalada, mas não saudável** (`INSTALLED_BUT_UNHEALTHY`): instalada, porém incompatível ou falhando na verificação de integridade/versão.
- **CLI não instalada** (`NOT_INSTALLED`): o executável canônico não foi encontrado para o usuário de runtime do ThreadCells.
- **Prontidão não verificada** (`UNKNOWN`): instalada e não comprovadamente indisponível, mas o provedor não consegue verificar de modo não interativo a autenticação ou prontidão com segurança.

Um provedor não verificado pode continuar iniciável quando seu comando está instalado e compatível e a única incógnita é o estado de autenticação. Uma inicialização ainda pode falhar com um prompt de login nativo do provedor; inspecione o terminal e conclua a autenticação do provedor fora do ThreadCells.

## Verifique a visão do usuário de runtime

A disponibilidade de um provedor depende da conta que executa o ThreadCells, não do seu shell interativo. Primeiro verifique pelo ThreadCells:

```bash
threadcells providers list
threadcells doctor
```

Em seguida, como usuário de runtime, verifique o binário esperado e sua versão. Para Codex:

```bash
command -v codex
codex --version
codex login status
```

Use o comando de status do próprio provedor quando ele existir. Não copie diretórios pessoais de credenciais de provedores para a conta de serviço. Autentique essa conta usando o fluxo compatível do provedor.

## Settings e Spawn Agent

Settings → Providers é a visualização de inventário e diagnósticos. Ela mostra identidade do adaptador, configuração, capacidades, presença do comando, versão, estado de autenticação e uma mensagem de pré-verificação segura para publicação.

Spawn Agent é a visualização de inicialização. Ela deriva seu estado habilitado/desabilitado do mesmo resultado de pré-verificação. Se as duas visualizações divergirem após uma atualização, trate isso como defeito do produto em vez de adivinhar qual rótulo está correto.

## Capacidades são específicas de cada provedor

Os adaptadores declaram se retomada, conclusão estruturada, seleção de modelo, controle de raciocínio, persistência de sessão e uso são compatíveis, condicionais ou não compatíveis. O ThreadCells não simula um recurso não compatível.

Codex é o adaptador de referência e fornece telemetria cumulativa de uso exata para campos de token compatíveis. Claude Code suporta algumas capacidades de uso e conclusão condicionalmente. Outros adaptadores podem não informar uso; seus campos em Statistics permanecem indisponíveis em vez de estimados.

## Configuração e segredos

A configuração do provedor é declarativa. Ela pode selecionar um adaptador instalado e configurações pertencentes ao adaptador, mas não pode importar caminho de binário, comando de shell, argumentos, variáveis de ambiente, senhas, tokens ou credenciais brutas.

`secret_refs` opacos podem nomear um segredo resolvido por código confiável do adaptador. As respostas públicas de listagem e exportação omitem ou ocultam seus valores. Pacotes de adaptadores de provedores são código confiável executável e devem ser instalados e revisados pelo operador do host.

## Solução de problemas

### O provedor mostra CLI não instalada

Execute `command -v` como a conta de serviço e compare seu `PATH` com o do seu shell. Instale o comando canônico do provedor somente se pretende usá-lo e, em seguida, reinicie ou atualize a pré-verificação.

### Instalada, mas autenticação necessária

Execute o fluxo oficial de login do provedor como usuário de runtime. A pré-verificação do ThreadCells nunca autentica em seu nome nem habilita configurações de desvio de permissões.

### Prontidão não verificada

O comando existe, mas não tem uma sonda de prontidão não interativa segura. Verifique a versão e faça um pequeno teste nativo do provedor. Uma inicialização do ThreadCells pode ser a primeira verificação definitiva de prontidão.

### Instalada, mas não saudável

Leia o motivo seguro da pré-verificação. Causas comuns são falha no comando de versão, uma versão conhecida como incompatível ou um executável que encerra inesperadamente. Atualize ou repare a CLI externa; não edite o registro de adaptadores para marcá-la como pronta.

### A inicialização falha apesar de Pronto

Abra a saída do terminal. As credenciais podem ter expirado após a pré-verificação, um modelo selecionado pode estar indisponível ou a integridade do serviço do provedor pode ter mudado.

Para detalhes avançados de integração, consulte [Criação de adaptadores de provedores](PROVIDER_ADAPTERS.md). Para saber o que um perfil de inicialização controla, consulte [Perfis](PROFILES.md).
