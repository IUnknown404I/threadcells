---
slug: control-plane-artifacts
source: docs/CONTROL_PLANE_ARTIFACTS.md
source_sha256: sha256:bbb3ff2ed634050407d78c0fff79f097ecc2c8f29b783d422a52469c624fd8b7
---
# Artefatos do plano de controle e fluxo de trabalho com IA

O ThreadCells publica documentos JSON Schema Draft 2020-12 para ProfileDefinition V1, ProviderConfiguration V1, AdapterManifest V1 e AdapterCapabilities V1 em `schemas/v1` no candidato local e em `cli_agent_orchestrator/public_schemas/v1` no wheel.

Use `threadcells profiles schema|example` ou `threadcells providers schema|example` para obter um documento inicial. Valide antes de importar. Falhas de campo são registros estáveis de ponteiro JSON, e não valores brutos refletidos. As importações pela UI, CLI e API chamam o mesmo serviço e criam revisões imutáveis.

## Fluxo de trabalho de artefatos assistido por IA

1. Busque o esquema, o exemplo e o prompt seguro de geração relevantes em `/api/v1/profiles/ai-prompt` ou `/api/v1/providers/ai-prompt`.
2. Peça ao modelo que retorne apenas um objeto JSON. Não forneça credenciais, caminhos privados, comandos executáveis, flags de shell ou comandos MCP não revisados.
3. Inspecione manualmente identificadores, referências de provedores, autoridade, ferramentas, tempos limite e instruções.
4. Execute `validate`; corrija cada problema de ponteiro JSON.
5. Importe somente após a revisão do operador. Importações que exigem ferramentas com curinga ou outra autoridade privilegiada precisam do caminho separado para operador confiável.
6. Use a prévia resolvida antes do lançamento e exporte após a importação para confirmar o artefato canônico com dados sensíveis removidos.

O JSON gerado por IA é uma entrada não confiável. Um documento plausível não instala código de adaptador, registra uma capacidade MCP, concede autorização de proprietário nem ignora a política do repositório. Os perfis integrados permanecem imutáveis, e as exportações nunca contêm credenciais de provedores nem permissões de lançamento.
