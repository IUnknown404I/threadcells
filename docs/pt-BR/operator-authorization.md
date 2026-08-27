---
slug: operator-authorization
source: docs/OPERATOR_AUTHORIZATION.md
source_sha256: sha256:543fc9c31e1ffe8e120aa726c819f0e9180d0f6ca92b28c9b4ce549d0025d4b1
---

# Autorização do operador

A autorização do operador protege alterações sensíveis do plano de controle em Settings. Ela é separada do acesso à Web UI comum: navegar por agentes, terminais, documentos e estatísticas não exige o segredo do operador.

Este recurso não é autenticação de usuário remoto. Mantenha o ThreadCells somente em loopback e siga [Acesso remoto](REMOTE_ACCESS.md) quando outra máquina precisar de acesso.

## Como funciona

O ThreadCells armazena um verificador derivado do segredo, nunca o segredo em texto simples. O servidor carrega esse verificador na inicialização. Informar o segredo correto cria uma sessão curta e segura de operador; as mutações protegidas permanecem bloqueadas depois que ela expira.

```text
Verifier configured
      ↓
Settings shows Locked
      ↓ enter operator secret
Unlock operator changes
      ↓
Short-lived authenticated session
      ↓ expires
Locked again
```

O tamanho mínimo do segredo do operador é exatamente **5 caracteres**. Quatro caracteres são rejeitados. É altamente recomendado um segredo maior, gerado aleatoriamente.

## Criar um verificador

Execute o comando independente como usuário administrativo em qualquer diretório de trabalho legível:

```bash
threadcells operator create-verifier --output /etc/threadcells/operator-verifier.json
```

O comando solicita o segredo sem exibi-lo e grava somente o verificador KDF com salt. Proteja o diretório que o contém contra modificações pela conta de serviço do ThreadCells, permitindo que essa conta leia o arquivo. Um layout adequado é:

```bash
sudo chown root:threadcells /etc/threadcells
sudo chmod 0750 /etc/threadcells
sudo chown root:threadcells /etc/threadcells/operator-verifier.json
sudo chmod 0640 /etc/threadcells/operator-verifier.json
```

Adapte o nome do grupo à conta de serviço usada pela sua instalação. Todo diretório pai no caminho também precisa ser confiável: o ThreadCells rejeita um verificador acessado por diretório pertencente ao serviço ou gravável por grupo/outros.

Não coloque o segredo ou JSON do verificador no repositório, banco de dados, logs, armazenamento do navegador, telemetria ou em uma solicitação de API fora da operação de desbloqueio.

## Configurar o servidor

Defina a referência absoluta do verificador no ambiente do servidor:

```bash
THREADCELLS_OPERATOR_VERIFIER_FILE=/etc/threadcells/operator-verifier.json
```

Reinicie somente o servidor ThreadCells e inspecione Settings → General → Operator authorization. O estado deve ser **Configured · Locked**, não **Not configured** nem **Configuration invalid**.

O endpoint de sessão informa somente estado seguro:

```bash
curl -s http://127.0.0.1:9889/operator/session
```

O resultado esperado inclui `"configured": true` e `"authenticated": false` antes do desbloqueio. Ele nunca retorna o caminho do verificador, salt, hash ou segredo.

## Desbloquear alterações protegidas

Em Settings, informe o segredo e escolha **Unlock operator changes**. A janela autenticada padrão é de cinco minutos. A UI mostra a expiração e volta ao estado bloqueado quando a sessão termina.

Chamadas protegidas de configurações falham enquanto bloqueadas e têm êxito durante a sessão autenticada. O navegador usa o cookie seguro de sessão curta do servidor; ele não persiste o segredo do operador.

O Full Cleanup reutiliza exatamente essa autoridade. A prévia continua disponível como inspeção de segurança somente leitura, enquanto a execução exige a sessão de operador atual e a confirmação padrão de ação permanente. A confirmação não solicita o segredo novamente. Não existe segredo separado de limpeza, credencial na URL, valor no armazenamento do navegador nem cópia durável em texto simples; expiração, novo bloqueio e limites de frequência permanecem inalterados.

## Substituir o segredo

Crie um novo verificador em um caminho administrativo temporário, valide sua propriedade e permissões e depois substitua atomicamente o arquivo configurado e reinicie o ThreadCells. Sessões de operador existentes devem ser tratadas como inválidas após a substituição.

A Web UI atual deliberadamente não oferece redefinição remota não autenticada nem gravador de verificador em Settings. O provisionamento pela CLI mantém o verificador sob propriedade do sistema operacional e evita criar um subsistema de segurança mais amplo.

## Inicialização XHigh do proprietário

O perfil integrado `critical_sol_xhigh_owner` está disponível por **Create Session & Spawn Agent**, **Add Agent** para uma sessão existente e a CLI local. Ambos os fluxos Web mostram o mesmo aviso de autoridade excepcional, exigem confirmação explícita e sessão de operador desbloqueada, emitem uma capacidade de uso único de curta duração vinculada a revisão/escopo e a consomem pelo caminho normal de inicialização. Add Agent vincula a capacidade à sessão existente e ao seu diretório de trabalho resolvido canonicamente; o operador não pode digitar um caminho substituto arbitrário.

O caminho da CLI local exige `--owner-xhigh` e confirmação interativa explícita. Ele emite e consome pelo loopback a mesma classe de capacidade de uso único. Não há atalho reutilizável de bypass/header: checkbox/confirmação ausente, segredo de operador ausente ou errado, escopo incompatível ou concessão reutilizada falham de modo fechado. O cliente Web autenticado recebe a capacidade opaca uma única vez, apenas para executar a inicialização correspondente; o segredo do operador nunca é retornado. Nenhum valor é copiado para metadados de agente/sessão, prompts de provedor, transcrições de terminal, logs ou armazenamento do navegador. Esses caminhos de inicialização não autorizam filhos nem enfraquecem mutações protegidas de Settings.

## Solução de problemas

- **Não configurado:** a variável de ambiente está ausente ou vazia. Confirme que ela chega ao processo real do servidor e reinicie.
- **Configuração inválida:** inspecione os logs do servidor em busca do motivo seguro de validação. Verifique esquema JSON, caminho absoluto, legibilidade, proprietário, modo e todos os diretórios pai. Não recrie um verificador válido apenas para ocultar um problema de caminho ou propriedade.
- **O segredo correto é rejeitado:** assegure que o gerador e o servidor usam o mesmo arquivo de verificador e que não há processo de servidor antigo ainda em execução.
- **O desbloqueio é bem-sucedido e logo bloqueia:** confirme que os cookies do navegador são aceitos e que o relógio do sistema está correto.
- **O desbloqueio funciona localmente, mas alterações protegidas falham por um proxy HTTPS:** defina `THREADCELLS_TRUSTED_PROXY_ORIGINS` como a origem HTTPS pública exata (por exemplo, `https://threadcells.example.com`) no ambiente de serviço do ThreadCells e reinicie. Não adicione caminhos, curingas nem origens não autenticadas.
- **A criação do verificador falha em um diretório não relacionado:** use uma compilação atual do ThreadCells. O comando independente não deve inspecionar um `.env` do diretório de trabalho.

Consulte [Modelo de segurança](SECURITY_MODEL.md) para as premissas de confiança ao redor.
