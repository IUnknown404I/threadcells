---
slug: profiles
source: docs/PROFILES.md
source_sha256: sha256:c378cfce9445d9171027ab61113863019c5478942bbdf218c8cdd1a6a608c552
---

# Perfiles

Un perfil es una política de lanzamiento reutilizable para un agente. Responde: ¿qué proveedor y modelo se deben ejecutar, cuánto razonamiento debe usar, qué rol e instrucciones debe recibir y qué capacidades o autoridad están permitidas?

La mayoría de las personas deberían empezar con un perfil integrado e inspeccionar su vista previa resuelta. No necesitas crear JSON sin procesar para el uso normal.

## Qué controla un perfil

Un perfil resuelto puede incluir:

- configuración de proveedor, modelo y esfuerzo de razonamiento;
- rol, como supervisor, desarrollador, revisor o especialista;
- instrucciones y referencias a skills;
- herramientas permitidas y capacidades MCP;
- tiempos de espera y comportamiento de ejecución;
- restricciones de autoridad de escritor o de nivel de propietario;
- si está pensado para permanecer residente o completar trabajo acotado.

La potencia del modelo y el rol de orquestación son independientes. Un modelo potente no es automáticamente un supervisor, y el nombre de un perfil no determina cómo se carga la capacidad.

## Perfiles integrados

ThreadCells incluye perfiles inmutables para roles comunes, incluidos supervisores habituales y más potentes, desarrolladores, revisores, trabajo de arquitectura y estrategia, trabajo de frontend/UI y un ejecutor XHigh con autorización de propietario limitada.

Ejemplos:

- `supervisor_terra_medium`: el orquestador continuo predeterminado para flujos de trabajo ordinarios y de riesgo medio; descompone, delega, revisa, acepta e integra.
- `supervisor_sol_medium`: el supervisor centrado en la orquestación para flujos de trabajo arriesgados, entre módulos, sensibles a la arquitectura o sensibles al ciclo de vida.
- `developer_terra_medium`: implementación rutinaria, acotada y con poca ambigüedad.
- `developer_terra_high`: trabajo de producto importante, defectos y refactorizaciones difíciles pero acotados, y calidad semántica pública.
- `developer_sol_medium`: trabajo entre subsistemas con razonamiento intensivo e invariantes sutiles.
- `reviewer_sol_high`: revisión independiente para cambios arriesgados o integrados.
- `critical_sol_xhigh_owner`: un perfil excepcional de propietario-ejecutor con un límite de autorización separado.

Los perfiles integrados son inmutables para que un ID conocido no pueda cambiar de significado silenciosamente. Para personalizar uno, duplícalo; la copia recibe una identidad personalizada.

## Elegir un perfil

Usa el perfil menos especializado que pueda asumir la tarea de forma fiable:

| Tarea | Punto de partida |
| --- | --- |
| Cambio de código pequeño y acotado | desarrollador |
| Revisión de aceptación independiente | revisor |
| Varios flujos de trabajo dependientes | supervisor |
| Diseño de arquitectura o migración | especialista en arquitectura/estrategia |
| Implementación de interfaz de producto | especialista en frontend o UI/UX |
| Ejecución crítica de propietario de frontera | solo XHigh autorizado por el propietario |

Más razonamiento y una autoridad más amplia consumen capacidad e incrementan las consecuencias. Deben corresponder a la tarea, no convertirse en valores predeterminados.

Un supervisor Sol no implica un desarrollador Sol. Debe seguir asignando la implementación rutinaria a desarrolladores Terra y reservar `developer_sol_medium` para trabajo cuya corrección dependa de un razonamiento sutil entre sistemas.

## Reintentos y escalado

ThreadCells clasifica los intentos de implementación fallidos antes de seleccionar otro agente:

| Clase de fallo | Respuesta canónica |
| --- | --- |
| `OPERATIONAL_FAILURE` | Puede ser válido reintentar en el mismo nivel. |
| `MECHANICAL_INCOMPLETE` | Permite una corrección acotada en el mismo nivel. |
| `SEMANTIC_QUALITY_FAILURE` | Escala el nivel de implementación; nunca realices un tercer intento semántico en el mismo nivel. |
| `BOUNDARY_COMPLEXITY_UNDERESTIMATED` | Selecciona un desarrollador más potente. |
| `CRITICAL_SYSTEMIC_BOUNDARY` | Usa `critical_sol_xhigh_owner` con autorización del propietario. |

La ruta de escalado normal es `developer_terra_medium` → `developer_terra_high` → `developer_sol_medium`. XHigh se reserva para autoridad sistémica realmente crítica, como seguridad, concurrencia exactamente una vez, Housekeeping destructivo, migraciones o recuperación peligrosa. Las pruebas aprobadas son evidencia necesaria, pero por sí solas no demuestran la calidad semántica.

## Vista previa resuelta

Settings → Profiles muestra tanto el artefacto guardado como su **vista previa resuelta**. Usa la vista previa antes de lanzar para verificar el proveedor, modelo, razonamiento, rol, herramientas, autoridad, tiempos de espera e instrucciones reales después de aplicar valores predeterminados y referencias.

Los nuevos lanzamientos capturan atómicamente esa revisión resuelta. Editar el perfil personalizado después crea otra revisión inmutable y no reescribe el significado histórico de una sesión existente.

Las sesiones antiguas creadas antes de las instantáneas de revisión pueden mostrar `legacy/unavailable snapshot`. ThreadCells no inventa configuración pasada.

## Crear un perfil personalizado

El camino más seguro es:

1. Abre Settings → Profiles.
2. Elige el perfil integrado más cercano.
3. Duplícalo.
4. Da a la copia un nombre claro basado en el rol.
5. Cambia los campos mínimos necesarios.
6. Inspecciona la vista previa resuelta.
7. Úsalo para un lanzamiento de prueba acotado antes de un trabajo más amplio.

Las ediciones personalizadas crean revisiones. Un perfil al que hace referencia el historial se deshabilita en lugar de eliminarse de forma destructiva.

## Autoridad especializada y de propietario

Las importaciones no confiables no pueden crear autoridad de propietario-ejecutor, XHigh, sin restricciones ni `danger-full-access`. Un operador autenticado puede crear una revisión personalizada privilegiada solo mediante el plano de control protegido, y el servidor sigue requiriendo la concesión de propietario de un solo uso aplicable al lanzar.

El perfil integrado `critical_sol_xhigh_owner` se puede seleccionar en ambos flujos de lanzamiento Web: al crear una sesión o al añadir un agente a una sesión existente. Cada uno muestra el bloque de autoridad excepcional y requiere confirmación explícita además del desbloqueo de operador de corta duración antes de acuñar y consumir una capacidad de lanzamiento normal. Add Agent limita esa capacidad a la sesión existente y al directorio de trabajo canónico heredado/del proyecto. La CLI local ofrece la misma clase de autoridad mediante `--owner-xhigh` y confirmación interactiva. Ninguna de estas rutas crea una omisión reutilizable de la API ni autoriza otros perfiles, terminales secundarios o cambios no relacionados de Settings.

## Perfiles y capacidad

Una sesión de supervisor o propietario de nivel superior consume capacidad de supervisor residente. Un hijo delegado consume una ranura de contexto de trabajo. La ejecución del proveedor y la ejecución pesada se cargan por separado según la actividad, no simplemente porque un perfil contenga `supervisor` o `reviewer` en su nombre.

Consulta [Capacidad y modelo de recursos](RESOURCE_MODEL.md) antes de aumentar la concurrencia de perfiles potentes.

## Importación y exportación avanzadas

La CLI expone el esquema y ejemplos actuales:

```bash
threadcells profiles schema
threadcells profiles example
threadcells profiles export
threadcells profiles validate /path/to/profile.json
threadcells profiles import /path/to/profile.json
```

Valida antes de importar. Las importaciones usan la misma validación del servicio que la UI y no pueden introducir comandos MCP ejecutables. Pueden hacer referencia a configuraciones de proveedor instaladas e identificadores de capacidades registrados.

No edites manualmente filas de base de datos ni copies instrucciones privadas, rutas del sistema de archivos, credenciales o estado interno del propietario en un artefacto de perfil público.

## Errores habituales

- Elegir un perfil solo por el nombre de su modelo.
- Dar a un trabajador cotidiano autoridad de nivel de propietario.
- Editar un perfil personalizado sin comprobar la vista previa resuelta.
- Esperar que una edición cambie sesiones que ya se están ejecutando.
- Importar valores de secretos sin procesar en lugar de referencias aprobadas.
- Tratar un perfil como instalación de proveedor; la CLI seleccionada aún debe estar lista.

A continuación, consulta [Flujos de trabajo y resultados duraderos](WORKFLOWS_AND_RESULTS.md) para saber cómo cooperan los perfiles de supervisor y trabajador.
