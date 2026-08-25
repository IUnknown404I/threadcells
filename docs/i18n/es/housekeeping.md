---
source_path: docs/HOUSEKEEPING.md
source_sha256: 9fec11f36bc4e4ba02212122e6ff393b8cea51beed5819e3dbe0987b205b1841
---

# Housekeeping

Housekeeping recupera artefactos de runtime solo cuando ThreadCells puede demostrar que cumplen los requisitos. Es intencionalmente conservador: un recurso desconocido, ilegible, activo, referenciado o modificado se protege en vez de suponer que es seguro eliminarlo.

![Housekeeping de ThreadCells en vivo con salud de disco, copias de seguridad protegidas, programaciones y política de limpieza](/media/screenshots/threadcells-housekeeping.webp)

## Qué se puede limpiar

Según la antigüedad y la evidencia de propiedad, un plan puede incluir:

- rutas temporales caducadas con marcadores de propiedad de ThreadCells;
- adjuntos de terminal antiguos no referenciados por un terminal activo;
- registros aptos para compresión o limpieza por retención;
- grupos de procesos de navegador huérfanos identificados por identidad exacta de proceso;
- revisiones y cachés del navegador no referenciadas por metadatos activos;
- contenedores y volúmenes etiquetados por ThreadCells cuyo propietario está muerto y no tiene referencias;
- cachés de paquetes de confianza con una acción de recuperación medible;
- candidatos/releases inactivos representados por metadatos canónicos de preparación;
- paneles de runtime de terminal exactamente cerrados y descendientes de procesos cuya terminal duradera ya está cerrada y cuya identidad de proceso sigue coincidiendo;
- worktrees hijos administrados pendientes de limpieza después de que se confirme y revalide su límite de resultado/retiro duradero;
- worktrees vinculados limpios e inactivos cuyo HEAD ya está contenido en una referencia Git duradera configurada explícitamente;
- cachés reproducibles marcadas/evidencia generada situadas directamente bajo una raíz de caché aprobada, después de que su propietario esté muerto y haya vencido la retención.

Housekeeping no elimina a ciegas repositorios de código fuente, worktrees activos o desconocidos, terminales en ejecución, archivos abiertos, releases actuales/de rollback, candidatos preparados ni copias de seguridad. Los worktrees vinculados se retiran mediante `git worktree remove` y `git worktree prune`, nunca con eliminación recursiva genérica. Retirar el runtime de un terminal cerrado no elimina su historial duradero de sesión, agente, Inbox, resultados ni flujos de trabajo.

Un directorio reproducible debe ser hijo inmediato de una raíz configurada y contener `.threadcells-reproducible.json`:

```json
{"schema_version":1,"owner":"threadcells","kind":"cache","created_at":1790000000,"owner_pid":12345}
```

Los tipos compatibles son `cache`, `generated`, `test_evidence` y `candidate`. Los marcadores ausentes o no válidos, enlaces simbólicos, escapes de ruta, propietarios vivos y rutas dentro de la ventana de retención permanecen protegidos.

Los despliegues también pueden nombrar prefijos exactos de caché propiedad de ThreadCells para cachés de CI compatibles con versiones anteriores. Estas entradas permanecen limitadas a hijos directos de la raíz aprobada propiedad del runtime y requieren que haya vencido la retención, además de las mismas comprobaciones de proceso activo e identidad en el momento de ejecución. Los prefijos no listados, incluidos los artefactos ambiguos de candidatos de release, permanecen protegidos.

## Primero planificar, después ejecutar

Un plan dry-run es de solo lectura. Cada candidato incluye su categoría, identidad/huella canónica, acción propuesta, bytes totales, bytes estimados a recuperar cuando se conocen, motivo de retención y motivo de protección. Los resúmenes por clase informan por separado las superficies accionables/recuperables y las conservadas/protegidas, para que una clase protegida grande no quede oculta como cero bytes.

```text
Inspeccionar estado actual
      ↓
Crear plan inmutable y plan_id
      ↓ revisión del operador
Ejecutar plan_id exacto
      ↓
Reconstruir conjunto protegido bajo bloqueo
      ↓
Revalidar cada candidato inmediatamente antes de actuar
      ↓
Informar elementos recuperados, omitidos, modificados y fallidos
```

Si el conjunto de candidatos cambia entre la planificación y la ejecución, la ejecución manual rechaza el plan obsoleto sin cambiar recursos. Cada candidato restante se comprueba de nuevo justo antes de la mutación.

## Ejemplo manual seguro

Desde el entorno instalado, solicite primero salida JSON:

```bash
threadcells-housekeeping --dry-run --json
```

Revise cada candidato y copie el `plan_id` devuelto. Ejecute solo ese plan inspeccionado:

```bash
threadcells-housekeeping --plan-id PLAN_ID_FROM_DRY_RUN
```

No automatice la extracción de `plan_id` y la ejecución inmediata hasta entender el plan. Un dry-run nunca implica aprobación para eliminar.

## Filosofía del conjunto protegido

El conjunto protegido combina terminales y worktrees activos, propiedad de escritor/flujo de trabajo, linaje de código fuente/runtime actual, releases activos y de rollback, candidatos preparados, revisiones de navegador referenciadas, archivos abiertos, identidad de inicio de proceso vivo e identidad de terminal, metadatos de referencia de contenedores, copias de seguridad y bloqueos compartidos.

Los detalles importan para la implementación, pero la regla para el operador es sencilla: **la ausencia de evidencia no es evidencia de que un recurso esté muerto**. Si la protección no puede establecerse con precisión, Housekeeping lo omite e informa el motivo.

## Programaciones

Settings → Housekeeping separa política, programación, planificación, ejecución e informes. Las formas de programación compatibles incluyen:

- un intervalo frecuente de 15 minutos a 365 días, como `6h`;
- una programación UTC semanal, como `Sun 04:00 UTC`;
- limpieza por presión de disco mediante `on_red`.

Los temporizadores instalados pueden sondear cada 15 minutos, con activación inicial escalonada para que las comprobaciones frecuentes y semanales normalmente no colisionen. Los recibos duraderos evitan que una clase de programación se ejecute dos veces antes de su vencimiento. Un sondeo programado que encuentra el motor canónico de Housekeeping ya activo termina correctamente como omitido e intenta de nuevo más tarde; la contención manual del bloqueo sigue siendo un error. Una ejecución programada crea y ejecuta su plan debido bajo un único bloqueo de servicio; no reutiliza un plan manual aprobado por una persona.

Los cambios de Housekeeping y la ejecución manual están protegidos por [Autorización del operador](OPERATOR_AUTHORIZATION.md).

## Comportamiento ante presión de disco

En YELLOW, inspeccione el crecimiento y ejecute un plan en seco. En RED, ThreadCells puede admitir un arrendamiento pesado de Housekeeping seguro para recuperación aunque se pueda denegar el trabajo pesado ordinario. Los planes de presión ordenan primero los mayores candidatos demostrablemente seguros y muestran las clases protegidas dominantes, pero la limpieza sigue contando como una ejecución Heavy y no elude ninguna protección de candidatos.

La recuperación de caché de paquetes se informa como desconocida/cero cuando el comando no puede demostrar los bytes; ThreadCells no anuncia recuperación estimada.

## Informes y fallo parcial

El informe más reciente registra la identidad del plan/ejecución, el estado de recursos, las estimaciones, los resultados reales, los resultados por candidato y códigos de motivo estables. El fallo de un candidato no debilita la protección de candidatos posteriores ni oculta éxitos independientes.

Después de una ejecución, verifique la presión de disco e inspeccione las entradas omitidas/fallidas. Vuelva a planificar antes de otra ejecución; no reutilice un plan antiguo tras cambios de estado.

## Copias de seguridad y releases

Las copias de seguridad son solo de inventario. Las decisiones de retención para el medio de backup pertenecen a la política de backup del operador, no a Housekeeping automático.

La limpieza de releases y candidatos comparte el bloqueo canónico de preparación y requiere metadatos de referencia de confianza. Los runtimes activos y de rollback permanecen protegidos. Consulte [Actualización](UPGRADING.md).

Los servicios programados de Housekeeping instalados reciben el grupo limitado de mantenimiento de releases necesario para recuperar un release inmutable elegible. El plano de control principal y los procesos ordinarios de agentes no lo reciben. Una ejecución manual/de API sin esa autoridad omite la eliminación de releases con `RELEASE_ADMIN_GROUP_REQUIRED`, continúa con la limpieza segura independiente y deja que el servicio programado recupere el release más tarde mediante el mismo motor de planificar/ejecutar.

La protección de rutas abiertas inventaría todos los procesos propiedad de la cuenta de runtime ThreadCells configurada, independientemente de qué cuenta autorizada invoque un plan manual. Las demás cuentas del host quedan fuera del límite de propiedad para el estado desechable de ThreadCells; las entradas privadas ilegibles de `/proc` de esas cuentas no deshabilitan la limpieza de todo el host. Una identidad de runtime desconocida o cualquier incertidumbre al inspeccionar un proceso de cuenta de runtime sigue fallando de forma cerrada.

## Errores habituales

- Eliminar directamente un directorio de worktree para recuperar espacio.
- Tratar un recuento estimado de bytes como recuperación garantizada.
- Ejecutar un plan que no se ha inspeccionado.
- Suponer que un PID detenido demuestra suficientemente que un grupo de navegador/proceso es el antiguo.
- Esperar que Housekeeping elimine las copias de seguridad.
- Elevar los umbrales de disco en vez de abordar el crecimiento sostenido.
