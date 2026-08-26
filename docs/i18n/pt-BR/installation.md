---
slug: installation
source: docs/INSTALLATION.md
source_sha256: sha256:a189b43dfe9b1b57700c30e1f4d29ecf8c1acf1ea30782bdfb8bc6cc89c2cdea
---

# Instalação

Este guia explica o caminho de instalação local compatível e por que o ThreadCells é instalado a partir de um candidato verificado. Se você quer apenas os comandos, use a [configuração rápida](../QUICK_SETUP.md).

## Referência compatível

A prévia técnica atual oferece suporte a um único host Linux Ubuntu/Debian. O ThreadCells pressupõe uma conta de operador confiável e um checkout Git local. Outras distribuições Linux podem funcionar, mas não são a referência compatível; macOS e Windows podem acessar a Web UI remotamente, mas não são hosts ThreadCells compatíveis.

## Pré-requisitos

Instale ou verifique:

- Python 3 e suporte a `venv`;
- Git;
- tmux;
- Node.js e npm para compilar a Web UI empacotada;
- utilitários POSIX comuns usados pelos scripts de lançamento e serviço;
- uma CLI de provedor compatível, instalada e autenticada para a conta que executará o ThreadCells.

Verifique os comandos importantes:

```bash
python3 --version
git --version
tmux -V
node --version
npm --version
```

O ThreadCells pode registrar adaptadores cujas CLIs não estejam presentes. Isso não é uma falha de instalação; somente os provedores que você pretende iniciar precisam estar prontos. Consulte [Provedores](PROVIDERS.md).

## Onde o estado fica

Por padrão, o estado operacional fica em:

```text
~/.aws/cli-agent-orchestrator/
```

O nome histórico do diretório é mantido por compatibilidade. Ele pode conter o banco de dados SQLite, logs, worktrees gerenciados, contexto de agente, anexos, artefatos de provedor e outros estados de runtime. Defina `CAO_HOME_DIR` antes da primeira inicialização para escolher outro local absoluto.

O aplicativo instalado e seu estado de runtime são diferentes:

- o **candidato/instalação** contém código versionado e recursos Web estáticos;
- a **raiz de estado** contém o banco de dados, dados mutáveis do operador e arquivos secretos opcionais e restritos pertencentes ao ThreadCells, como o token de bot do Telegram;
- as CLIs de provedores podem manter suas próprias credenciais e histórico de implantação em outros locais.

Faça backup do estado mutável antes de substituir uma instalação. Nunca faça commit no Git do estado de runtime nem das credenciais de provedores.

## Por que usar um candidato local?

Um candidato é um diretório com formato de lançamento, compilado de uma revisão exata do código-fonte. Seu manifest e checksums permitem verificar o que será executado antes de modificar uma instalação. A preparação e a promoção podem preservar o candidato anterior para rollback.

Essa disciplina é mais deliberada que executar diretamente de um checkout que muda, mas impede que a Web UI, o código Python, a documentação e a identidade da compilação venham silenciosamente de revisões diferentes.

## Compile o candidato

Na raiz do repositório:

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.0a2-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

Resultado esperado: o verificador aceita o manifest, os checksums, a documentação empacotada e os arquivos do aplicativo. Não instale um candidato cuja verificação falhe.

## Visualize e instale

Escolha um prefixo absoluto que a conta de runtime possa executar. O prefixo local ao repositório abaixo é conveniente para avaliação:

```bash
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --dry-run
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --prefix "$PWD/.threadcells"
```

A simulação vem intencionalmente primeiro. Revise a origem e o destino e, então, execute a instalação real.

## Verifique a CLI instalada

```bash
"$PWD/.threadcells/venv/bin/threadcells" info
"$PWD/.threadcells/venv/bin/threadcells" doctor
"$PWD/.threadcells/venv/bin/threadcells" providers list
```

`doctor` é somente leitura. Resolva os utilitários de sistema obrigatórios que estiverem ausentes. A saída do provedor deve distinguir um adaptador conhecido de uma CLI instalada e utilizável.

## Inicie localmente

```bash
"$PWD/.threadcells/venv/bin/threadcells-server" --host 127.0.0.1 --port 9889
```

Em outro shell:

```bash
curl -fsS http://127.0.0.1:9889/health
```

Abra `http://127.0.0.1:9889`. Verifique Settings → About e confirme que a versão e a revisão correspondem ao candidato que você verificou.

Para uma instalação persistente, use o mecanismo canônico de serviço/implantação do repositório descrito em [Implantação](DEPLOYMENT.md). Não improvise um endereço público de escuta.

## Falhas iniciais

- **`python3 -m venv` falha:** instale o pacote venv do Python da distribuição.
- **`tmux` ausente:** instale-o antes de iniciar agentes; a persistência de terminais depende dele.
- **Não é possível compilar os recursos Web:** use a referência compatível de Node/npm, instale as dependências fixadas e compile o candidato novamente.
- **O provedor informa CLI not installed:** instale o comando canônico desse provedor para o usuário de runtime ou escolha um provedor já preparado.
- **O provedor está instalado mas não autenticado:** conclua o fluxo de login do provedor como usuário de runtime e repita a verificação prévia.
- **A porta 9889 está ocupada:** pare o processo local conflitante ou escolha outra porta de loopback e use-a de forma consistente.
- **O navegador de outra máquina não consegue conectar:** isso é normal para um listener de loopback. Use [Acesso remoto](REMOTE_ACCESS.md).

## Limites de remoção

Remover um prefixo de instalação não remove com segurança o estado operacional, credenciais de provedor, repositórios Git, worktrees, backups nem definições de serviço. Pare o ThreadCells, crie um backup verificado e identifique cada uma dessas categorias separadamente. Use Housekeeping para artefatos de runtime elegíveis; não exclua recursivamente a raiz de estado como atalho para desinstalar.

Depois, siga [Seu primeiro projeto e agente](FIRST_AGENT.md).
