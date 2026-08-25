---
source_path: docs/CONTROL_PLANE_ARTIFACTS.md
source_sha256: bbb3ff2ed634050407d78c0fff79f097ecc2c8f29b783d422a52469c624fd8b7
---

# Artefactos del plano de control y flujo de trabajo con IA

ThreadCells publica documentos JSON Schema Draft 2020-12 para ProfileDefinition V1, ProviderConfiguration V1, AdapterManifest V1 y AdapterCapabilities V1 en `schemas/v1` dentro del candidato local y en `cli_agent_orchestrator/public_schemas/v1` dentro de la wheel.

Use `threadcells profiles schema|example` o `threadcells providers schema|example` para obtener un documento inicial. Valídelo antes de importarlo. Los fallos de campos son registros estables con punteros JSON, no valores sin procesar reflejados. Las importaciones desde UI, CLI y API llaman al mismo servicio y crean revisiones inmutables.

## Flujo de trabajo de artefactos asistido por IA

1. Obtenga el esquema, el ejemplo y el prompt de generación seguro correspondientes desde `/api/v1/profiles/ai-prompt` o `/api/v1/providers/ai-prompt`.
2. Pida al modelo que devuelva un único objeto JSON. No proporcione credenciales, rutas privadas, comandos ejecutables, indicadores de shell ni comandos MCP sin revisar.
3. Inspeccione manualmente los identificadores, las referencias a proveedores, la autoridad, las herramientas, los tiempos de espera y las instrucciones.
4. Ejecute `validate`; resuelva cada incidencia de puntero JSON.
5. Importe solo después de la revisión del operador. Las importaciones que requieran herramientas comodín u otra autoridad privilegiada necesitan la ruta independiente de operador de confianza.
6. Use la vista previa resuelta antes del inicio y exporte después de importar para confirmar el artefacto canónico redactado.

El JSON generado por IA es una entrada no confiable. Un documento verosímil no instala código de adaptador, registra una capacidad MCP, concede autorización del propietario ni elude la política del repositorio. Los perfiles integrados permanecen inmutables y las exportaciones nunca contienen credenciales de proveedores ni permisos de inicio.
