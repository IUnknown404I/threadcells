---
slug: profiles
source: docs/PROFILES.md
source_sha256: sha256:2bef7848e092db4dfc8e667bf98ea781cbae3ac77e80f02e25cc2500c7ad7776
---

# Perfis

Um perfil é uma política reutilizável de inicialização para um agente. Ele responde: qual provedor e modelo devem ser executados, quanto raciocínio deve usar, qual papel e instruções deve receber e quais capacidades ou autoridade são permitidas?

A maioria dos usuários deve começar com um perfil integrado e inspecionar sua prévia resolvida. Para o uso normal, não é preciso criar JSON bruto.

## O que um perfil controla

Um perfil resolvido pode incluir:

- configuração do provedor, modelo e esforço de raciocínio;
- papel, como supervisor, desenvolvedor, revisor ou especialista;
- instruções e referências de skills;
- ferramentas permitidas e capacidades MCP;
- timeouts e comportamento de execução;
- restrições de autoridade de escritor ou nível de proprietário;
- se ele deve permanecer residente ou concluir trabalho delimitado.

Poder do modelo e papel de orquestração são separados. Um modelo forte não é automaticamente um supervisor, e o nome de um perfil não determina como a capacidade é cobrada.

## Perfis integrados

O ThreadCells inclui perfis imutáveis para papéis comuns, inclusive supervisores cotidianos e mais fortes, desenvolvedores, revisores, trabalho de arquitetura e estratégia, trabalho de frontend/UI e um executor XHigh estreitamente autorizado pelo proprietário.

Exemplos:

- `supervisor_terra_medium`: o supervisor cotidiano para decomposição e integração comuns.
- `supervisor_sol_medium`: orquestração mais forte para trabalho importante ou entre módulos.
- `developer_terra_medium` e `developer_sol_medium`: papéis de implementação delimitada.
- `reviewer_sol_high`: revisão independente para mudanças arriscadas ou integradas.
- `critical_sol_xhigh_owner`: um perfil excepcional de proprietário-executor com limite de autorização separado.

Os perfis integrados são imutáveis para que um ID familiar não possa mudar de significado silenciosamente. Para personalizar um, duplique-o; a cópia recebe uma identidade personalizada.

## Escolher um perfil

Use o perfil menos especializado que possa assumir a tarefa de maneira confiável:

| Tarefa | Ponto de partida |
| --- | --- |
| Pequena alteração delimitada de código | developer |
| Revisão independente de aceitação | reviewer |
| Várias frentes de trabalho dependentes | supervisor |
| Projeto de arquitetura ou migração | especialista de arquitetura/estratégia |
| Implementação de UI do produto | especialista de frontend ou UI/UX |
| Execução crítica de proprietário de fronteira | somente XHigh autorizado pelo proprietário |

Mais raciocínio e autoridade mais ampla consomem capacidade e aumentam as consequências. Eles devem refletir a tarefa, não se tornar padrões.

## Prévia resolvida

Settings → Profiles mostra tanto o artefato salvo quanto sua **prévia resolvida**. Use a prévia antes da inicialização para verificar o provedor, modelo, raciocínio, papel, ferramentas, autoridade, timeouts e instruções efetivos depois que padrões e referências são aplicados.

Novas inicializações capturam atomicamente essa revisão resolvida. Editar o perfil personalizado mais tarde cria outra revisão imutável e não reescreve o significado histórico de uma sessão existente.

Sessões antigas criadas antes dos snapshots de revisão podem mostrar `legacy/unavailable snapshot`. O ThreadCells não fabrica configurações passadas.

## Criar um perfil personalizado

O caminho mais seguro é:

1. Abra Settings → Profiles.
2. Escolha o perfil integrado mais próximo.
3. Duplique-o.
4. Dê à cópia um nome claro baseado no papel.
5. Altere os campos mínimos necessários.
6. Inspecione a prévia resolvida.
7. Use-o em uma inicialização de teste delimitada antes de trabalho mais amplo.

Edições personalizadas criam revisões. Um perfil referenciado pelo histórico é desabilitado em vez de apagado destrutivamente.

## Autoridade especializada e do proprietário

Importações não confiáveis não podem criar autoridade de proprietário-executor, XHigh, irrestrita ou `danger-full-access`. Um operador autenticado pode criar uma revisão personalizada privilegiada somente pelo plano de controle protegido, e o servidor ainda exige a concessão aplicável de proprietário de uso único na inicialização.

O perfil integrado `critical_sol_xhigh_owner` pode ser selecionado nos dois fluxos de inicialização Web: criar uma sessão ou adicionar um agente a uma sessão existente. Cada um mostra o bloco de autoridade excepcional e exige confirmação explícita mais o desbloqueio de operador de curta duração antes de emitir e consumir uma capacidade normal de inicialização. Add Agent delimita essa capacidade à sessão existente e ao diretório de trabalho canônico herdado/do projeto. A CLI local oferece a mesma classe de autoridade por meio de `--owner-xhigh` e confirmação interativa. Nenhum desses caminhos cria um desvio reutilizável de API nem autoriza outros perfis, terminais filhos ou mudanças não relacionadas de Settings.

## Perfis e capacidade

Um supervisor de nível superior ou sessão do proprietário consome capacidade de supervisor residente. Um filho delegado consome uma vaga de contexto de trabalho. Execução de provedor e execução pesada são cobradas separadamente com base na atividade, não apenas porque um perfil contém `supervisor` ou `reviewer` em seu nome.

Consulte [Modelo de capacidade e recursos](RESOURCE_MODEL.md) antes de aumentar a concorrência de perfis poderosos.

## Importação e exportação avançadas

A CLI expõe o esquema e os exemplos atuais:

```bash
threadcells profiles schema
threadcells profiles example
threadcells profiles export
threadcells profiles validate /path/to/profile.json
threadcells profiles import /path/to/profile.json
```

Valide antes de importar. As importações usam a mesma validação de serviço da UI e não podem introduzir comandos MCP executáveis. Elas podem referenciar configurações de provedores instalados e identificadores de capacidades registrados.

Não edite manualmente linhas do banco de dados nem copie instruções privadas, caminhos do sistema de arquivos, credenciais ou estado interno do proprietário para um artefato de perfil público.

## Erros comuns

- Escolher um perfil apenas pelo nome do modelo.
- Dar a um trabalhador cotidiano autoridade de nível de proprietário.
- Editar um perfil personalizado sem verificar a prévia resolvida.
- Esperar que uma edição altere sessões já em execução.
- Importar valores de segredo brutos em vez de referências aprovadas.
- Tratar um perfil como instalação de provedor; a CLI selecionada ainda precisa estar pronta.

Em seguida, consulte [Fluxos de trabalho e resultados duráveis](WORKFLOWS_AND_RESULTS.md) para saber como perfis de supervisor e trabalhador cooperam.
