---
slug: operations
source: docs/OPERATIONS.md
source_sha256: sha256:c1b5d152c38e373d316a3a27b417872f4f6943ad591e69ed7eeb9cf3e030ec27
---
# Operações

A operação rotineira do ThreadCells consiste principalmente em preservar quatro tipos de verdade: a identidade da compilação em execução, a responsabilidade pelo fluxo de trabalho, a capacidade disponível e o estado recuperável.

## Verificações diárias

Use Home, Agents, Settings → General e Settings → Housekeeping para responder:

- O servidor está íntegro e a compilação esperada está em execução?
- O disco e a capacidade estão GREEN, YELLOW ou RED?
- Quais supervisores e workers estão realmente ativos?
- Há resultados entregues, mas não incorporados?
- Um fluxo de trabalho está aguardando uma decisão do proprietário?
- Se o Telegram estiver ativado, Settings → Telegram mostra o estado seguro esperado de conexão/teste?

A visualização de capacidade na linha de comando é:

```bash
threadcells-resource-status
```

Use o endpoint local de integridade para monitorar o serviço:

```bash
curl -fsS http://127.0.0.1:9889/health
```

## Iniciar e parar

Execute `threadcells-server` em loopback ou use o serviço instalado canônico. Uma desconexão do navegador não interrompe agentes sustentados por tmux. Uma reinicialização de servidor compatível preserva runtimes de terminal legitimamente ativos e, em seguida, reidrata o estado durável de fluxos de trabalho abertos e de entrega da Inbox. Runtimes encerrados são retirados pela identidade exata de terminal/processo; os registros históricos de sessão e resultado não dependem de um painel tmux continuar ativo.

Antes de uma reinicialização planejada:

1. inspecione o trabalho de provedores e pesado ativo;
2. evite interromper uma mutação quando possível;
3. registre as identidades de compilação ativa e de reversão atuais;
4. faça backup e verifique a integridade do banco de dados para uma atualização;
5. reinicie somente os serviços ThreadCells necessários;
6. reconecte-se e verifique os fluxos de trabalho/resultados antes de tentar algo novamente.

Use Graceful Exit para o ciclo de vida do provedor. Encerrar o tmux à força ou excluir linhas do banco de dados manualmente pode separar o estado do terminal da verdade durável do fluxo de trabalho.

## Higiene de sessões e fluxos de trabalho

Um filho encerrado não é imediatamente descartável. Confirme que seu resultado durável foi entregue, lido, incorporado e confirmado. Em seguida, retire seus recursos de runtime, mantendo o histórico.

**Add Agent** destina-se ao ciclo de vida estável da sessão selecionada. A exclusão de sessão histórica e a exclusão de terminal encerrado visam identidades duráveis exatas e são rejeitadas enquanto houver um runtime ativo, fluxo de trabalho aberto/em recuperação, concessão de escrita, resultado pendente ou outra relação protegida.

O encerramento de um provedor não fecha uma missão aberta. Conclua explicitamente um fluxo de trabalho de nível superior somente após terminar todo o trabalho autorizado pelo proprietário. Use o bloqueio de proprietário apenas para um limite de decisão genuíno.

## Alterações de capacidade

Settings → Orchestration Capacity aplica alterações sem reiniciar o servidor. Reduções escoam; elas não encerram sessões ativas. Altere uma restrição por vez e observe se a fila pretendida melhora.

As mutações de capacidade exigem uma sessão de operador desbloqueada e são auditadas. Veja [Modelo de capacidade e recursos](RESOURCE_MODEL.md).

## Logs e evidências

Mantenha logs e histórico de resultados suficientes para diagnosticar uma execução com falha, mas não trate logs como a única verdade durável. O banco de dados, o resultado do fluxo de trabalho, o commit/diff do Git, o manifesto do candidato e as evidências de teste respondem, cada um, a perguntas diferentes.

Evite registrar prompts ou valores que contenham credenciais. Os erros públicos/de API do ThreadCells devem permanecer seguros para exibição.

## Housekeeping

O Housekeeping sempre segue o plano primeiro. Inspecione a lista de candidatos da simulação e a identidade do plano e, em seguida, execute explicitamente o plano exato. O executor recompõe a proteção atual e revalida cada candidato antes da mutação. Ele pode retirar runtimes de terminais comprovadamente encerrados e worktrees com limpeza pendente já reconhecida sem apagar o histórico durável.

Os backups são apenas de inventário e nunca são excluídos automaticamente. Recursos desconhecidos ou ativos permanecem protegidos. Veja [Housekeeping](HOUSEKEEPING.md).

## Disciplina de mudanças em produção

Para uma atualização:

1. compile e verifique um candidato imutável a partir de um commit exato;
2. preserve a instalação atual como reversão;
3. faça backup e verifique a integridade do banco de dados;
4. faça o staging pelo mecanismo canônico de implantação;
5. promova o candidato exato preparado;
6. reinicie somente os serviços necessários;
7. faça testes de fumaça de integridade, UI, preflight de provedor, autorização de operador, fluxos de trabalho, terminais e notificações globais configuradas do Telegram.

Não publique, envie, marque nem altere a exposição pública como parte incidental de uma implantação local. Veja [Atualização](UPGRADING.md) e [Implantação](DEPLOYMENT.md).

## Quando algo parece errado

Preserve evidências antes da limpeza ou de tentar novamente. Registre a identidade da compilação, IDs de sessão/terminal/fluxo de trabalho, mensagem segura de erro, janela de log relevante, status do Git e capacidade atual. Em seguida, use o guia [Solução de problemas](TROUBLESHOOTING.md), organizado por sintomas.
