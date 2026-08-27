---
slug: getting-started
source: QUICK_SETUP.md
source_sha256: sha256:a50fbb421c1933e4880f7cfd918bcef30466fdaa91cead7de958fc274887a9c9
---

# Configuração rápida do ThreadCells

Este é o caminho compatível mais rápido de um checkout do código-fonte até um servidor local do ThreadCells. Ele compila um candidato local imutável, verifica seu conteúdo, instala-o sob o repositório atual e escuta apenas em loopback.

Para pré-requisitos, explicações de falhas e instalação como serviço, use o [guia completo de instalação](docs/INSTALLATION.md).

## 1. Verifique o host

Atualmente, o ThreadCells é direcionado a Linux Ubuntu/Debian com Python 3, Git, tmux, Node.js/npm para a compilação Web e ao menos uma CLI de provedor compatível. Codex é o principal provedor testado.

Na raiz do repositório:

```bash
python3 --version
git --version
tmux -V
node --version
npm --version
```

## 2. Compile e verifique um candidato

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.3a0-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

Resultado esperado: a verificação é concluída com sucesso para o manifest, arquivos, checksums e Web UI empacotada do candidato. Um candidato é um diretório autocontido com formato de lançamento; mantê-lo imutável torna a compilação em execução identificável e o rollback mais prático.

## 3. Visualize e depois instale

```bash
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --dry-run
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --prefix "$PWD/.threadcells"
```

Resultado esperado: a execução de simulação explica seus destinos sem alterá-los; depois, a instalação cria `.threadcells` com um ambiente Python e comandos do ThreadCells.

## 4. Execute diagnósticos

```bash
"$PWD/.threadcells/venv/bin/threadcells" doctor
```

Resolva as verificações obrigatórias que falharem antes de iniciar agentes. Um provedor opcional pode continuar ausente; ele aparecerá como **CLI not installed** na UI.

## 5. Inicie o servidor

```bash
"$PWD/.threadcells/venv/bin/threadcells-server" --host 127.0.0.1 --port 9889
```

Abra `http://127.0.0.1:9889`.

Resultado esperado: Home carrega, Settings → About mostra a identidade da compilação em execução e esta documentação fica disponível em Docs.

Mantenha host e porta estritamente em loopback nesta primeira execução. Para outro computador, não mude o listener para `0.0.0.0`; use [Acesso remoto](docs/REMOTE_ACCESS.md).

O modelo operacional é deliberadamente breve: crie uma sessão, escolha um agente ou supervisor, dê-lhe o trabalho, acompanhe o fluxo de trabalho e intervenha somente diante de uma decisão explícita do proprietário ou de uma revisão final. A conclusão do provedor sozinha não fecha um fluxo de trabalho aberto.

## 6. Inicie um trabalho útil

Siga [Seu primeiro projeto e agente](docs/FIRST_AGENT.md). O [exemplo inicial seguro](examples/threadcells-starter/README.md) incluído também é um exercício delimitado de supervisor/desenvolvedor/revisor que não publica nem altera serviços.

## Parar e retomar

Pare o servidor em primeiro plano com `Ctrl-C`. Os terminais de agentes são apoiados por tmux e podem sobreviver à desconexão do navegador, mas não suponha que um servidor interrompido concluiu seus fluxos de trabalho. Reinicie o mesmo `threadcells-server` instalado, abra Agents e inspecione o estado atual e os resultados duráveis.

## Próximas leituras

- [Conceitos centrais](docs/CONCEPTS.md)
- [Provedores](docs/PROVIDERS.md) e [Perfis](docs/PROFILES.md)
- [Capacidade e modelo de recursos](docs/RESOURCE_MODEL.md)
- [Housekeeping](docs/HOUSEKEEPING.md)
- [Notificações do Telegram](docs/TELEGRAM_NOTIFICATIONS.md)
- [Backup e restauração](docs/BACKUP_AND_RESTORE.md)
- [Modelo de segurança](docs/SECURITY_MODEL.md)
