---
slug: provider-adapters
source: docs/PROVIDER_ADAPTERS.md
source_sha256: sha256:1b3bda3574765fd4b540f7460e14a1677a3d3dd58be8bf9d07f5fba0c53df1d9
---

# Criação de adaptadores de provedores

Este é um guia avançado para mantenedores que adicionam uma integração confiável de provedor. Operadores que escolhem entre provedores integrados devem começar por [Provedores](PROVIDERS.md).

A API V1 de adaptadores de provedores do ThreadCells é um limite de extensão de código confiável distinto dos plugins observadores. Instale adaptadores como pacotes Python revisados que registram objetos `ProviderAdapterDefinition` no grupo de entry points `threadcells.provider_adapters.v1`. Reinicie o candidato/runtime local após a instalação para que os entry points sejam redescobertos.

## Contrato

Uma definição de adaptador fornece:

- um `AdapterManifest` com `adapter_id` estável, API de plugin `1.0`, versão da implementação, descrição, capacidades e esquema JSON de configuração;
- um modelo Pydantic `AdapterSettings` para configurações declarativas;
- uma fábrica que aceita `ProviderLaunchContext` e configurações validadas;
- uma função de pré-verificação que retorna estado normalizado, instalação, autenticação, versão, compatibilidade, modelos, código de motivo e mensagem sem segredos.

O provedor retornado implementa início/retomada/cancelamento normalizados, estado/resultado de terminal, uso e semântica de integridade pelo ciclo de vida `BaseProvider` existente. Declare de forma honesta as capacidades não compatíveis e condicionais. Nunca sintetize uso que uma CLI não informou.

## Confiança e configuração

Pacotes de adaptadores são executáveis e, portanto, instalados somente pelo operador confiável do host. O JSON de registro não pode escolher binários nem injetar comandos. O ThreadCells rejeita recursivamente chaves de executável, comando, shell, argumento, flag, ambiente, credencial, senha, token e segredo. Segredos brutos nunca pertencem a `settings`; use `secret_refs` opacos e semânticos e resolva-os apenas dentro de código confiável do adaptador de acordo com a política de segredos da instalação.

Mantenha erros normalizados com códigos de motivo estáveis e mensagens seguras para publicação. A pré-verificação não deve alterar configurações do provedor nem autenticar em nome do operador.

## Exemplo

O código-fonte/candidato instalado inclui `examples/provider-adapters/threadcells-echo`, um pacote e manifesto determinísticos que demonstram o entry point, esquema, validação de configuração, ciclo de vida, pré-verificação e uso não compatível. Ele não é um provedor de modelo e fica desativado por padrão. Compile/teste-o de modo independente antes da instalação.

Os esquemas empacotados em `schemas/v1/adapter-manifest.schema.json` e `schemas/v1/capabilities.schema.json` são as referências de artefatos portáveis. A validação do contrato Python continua sendo a autoridade para o código instalado.

## A prontidão deve continuar verdadeira

Use o nome canônico do executável do provedor e uma sonda delimitada, sem mutação. A pré-verificação responde sobre instalação, compatibilidade, autenticação quando detectável com segurança e motivo de falha seguro para publicação. Ela não deve alegar que o registro do adaptador torna uma CLI disponível.

As APIs do registro, Settings e Spawn Agent projetam todas esse mesmo resultado. Adicione cobertura que prove que um comando não instalado é desabilitado, que uma falha de autenticação é distinguida de ausência e que um provedor instalado com autenticação genuinamente impossível de saber permanece rotulado como não verificado.

## O uso deve continuar verdadeiro

Prefira um evento estruturado nativo do provedor à análise de texto do terminal. Registre somente os campos que o provedor emite, preserve a identidade do checkpoint cumulativo e torne reinicialização/repetição idempotente. Nunca transforme uma métrica indisponível em zero nem estime custo a partir de tokens sem um contrato explícito do provedor.

## Lista de revisão

- ID estável do adaptador, versão, nome de exibição e esquema de configuração.
- Nenhum campo de executável, shell, argumento, ambiente ou segredo bruto selecionado pelo chamador.
- Pré-verificação delimitada sem mutação de configurações ou autenticação.
- Capacidades compatíveis/condicionais/não compatíveis honestas.
- Testes de ciclo de vida para início, estado, cancelamento e falha recuperável.
- Testes de uso exatos quando a telemetria é compatível.
- Testes de consistência entre registro/Settings/Spawn.
- Erros seguros para publicação que não contêm credenciais nem caminhos privados.
