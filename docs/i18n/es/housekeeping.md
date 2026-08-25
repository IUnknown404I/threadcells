---
slug: housekeeping
source: docs/HOUSEKEEPING.md
source_sha256: sha256:a7a64e54c6aaea5593617556363e4741ecb97759caa08e9d8bd3ba47d879927c
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
Inspect current state
      ↓
Build immutable plan and plan_id
      ↓ operator reviews
Execute exact plan_id
      ↓
Rebuild protected set under lock
      ↓
Revalidate each candidate immediately before action
      ↓
Report reclaimed, skipped, changed, and failed items
```

Si el conjunto de candidatos cambia entre la planificación y la ejecución, la ejecución manual rechaza el plan obsoleto sin cambiar recursos. Cada candidato restante se comprueba de nuevo justo antes de la mutación.

## Full Cleanup

La acción final de la zona de peligro en Settings → Housekeeping es **Delete all system files — Full Cleanup**. Usa el mismo inventario canónico, conjunto protegido, identidad de plan inmutable y comprobaciones de identidad en el momento de ejecución que el Housekeeping normal, pero aplica la máxima retención de seguridad demostrada: pueden pasar a ser elegibles las cachés reproducibles, los registros antiguos, los artefactos de compilación/candidatos/temporales, los worktrees que se puedan retirar de forma segura y todas las releases locales inactivas. La propiedad desconocida o la autoridad ambigua permanecen protegidas y se explican en el plan y el informe.

Full Cleanup solo está disponible cuando la verdad del ciclo de vida del backend demuestra que cada agente relevante está Ready, Exited o en un estado no ejecutor explícitamente equivalente. Los estados Working, Processing o Starting, las mutaciones del sistema de archivos en cola, la ejecución del proveedor, el trabajo Heavy, las operaciones de runtime y una identidad de ciclo de vida desconocida bloquean la ejecución. El servidor adquiere los límites canónicos de admisión y vuelve a comprobar esta barrera de inactividad inmediatamente antes de la mutación; si un agente pasa a estar activo después de la vista previa, la ejecución se cancela sin eliminar nada.

La vista previa es de solo lectura. La ejecución requiere el desbloqueo de operador existente y de corta duración y el modal de confirmación de acción permanente existente; no hay contraseña de Full Cleanup ni secreto almacenado por el cliente. La solicitud confirma un `plan_id` exacto de 64 caracteres y no incluye ninguna ruta arbitraria.

Cada candidato de Full Cleanup basado en una ruta lo ejecuta el root helper de ámbito reducido, activado por socket, después de que este vuelva a autenticar al operador de forma independiente, reconstruya el plan exacto, demuestre la barrera de inactividad y verifique que el plano de control aún mantiene todos los límites de admisión. El helper mueve cada candidato a una cuarentena exclusiva de root en el mismo sistema de archivos, bloquea el árbol de directorios capturado frente a mutaciones del usuario de runtime y después elimina únicamente las identidades verificadas mediante descriptores de directorio. Una identidad modificada se conserva y se incluye en el informe; la ejecución nunca recurre a una eliminación de rutas más débil como usuario de runtime. Los recursos de ciclo de vida ajenos al sistema de archivos siguen pasando por sus ejecutores transaccionales canónicos.

Después de un Full Cleanup correcto, solo queda la release local activa e inmutable de ThreadCells. Se eliminan todas las releases de rollback/recuperación inactivas demostradas, los metadatos de release se reconcilian atómicamente y el rollback local se informa como no disponible. La release activa y el puntero activo nunca pueden ser candidatos. Los agentes Ready siguen siendo utilizables: sus worktrees, autoridad de escritor, contexto actual, salida actual y demás estado de continuación permanecen protegidos. El historial Exited puede permanecer en SQLite después de limpiar de forma segura su salida del sistema de archivos; Full Output informa entonces de que la salida duradera no está disponible, en lugar de fallar o inventar texto.

Las copias de seguridad, la autoridad actual del código fuente y las herramientas, las credenciales/estado del proveedor, la base de datos SQLite y cualquier recurso no demostrado permanecen protegidos. Un segundo Full Cleanup genera de forma segura un plan procesable casi nulo, salvo los elementos que hayan pasado a ser elegibles o que antes estuvieran protegidos.

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

La autoridad protegida del flujo de trabajo se deriva de la identidad duradera del terminal raíz. La reconciliación de inicio y la frecuente cancelan los flujos de trabajo huérfanos que no son de recuperación y cuyo terminal raíz ya no existe, y después regeneran el conjunto protegido. Hasta que se reconcilie esa relación, la retirada de worktrees falla de forma cerrada para todo el inventario incierto.

## Programaciones

Settings → Housekeeping separa política, programación, planificación, ejecución e informes. Las formas de programación compatibles incluyen:

- un intervalo frecuente de 15 minutos a 365 días, como `6h`;
- una programación UTC semanal, como `Sun 04:00 UTC`;
- limpieza por presión de disco mediante `on_red`.

Los temporizadores instalados pueden sondear cada 15 minutos, con activación inicial escalonada para que las comprobaciones frecuentes y semanales normalmente no colisionen. Los recibos duraderos evitan que una clase de programación se ejecute dos veces antes de su vencimiento. Un sondeo programado que encuentra el motor canónico de Housekeeping ya activo termina correctamente como omitido e intenta de nuevo más tarde; la contención manual del bloqueo sigue siendo un error. Una ejecución programada crea y ejecuta su plan debido bajo un único bloqueo de servicio; no reutiliza un plan manual aprobado por una persona.

Los cambios de Housekeeping y la ejecución manual están protegidos por [Autorización del operador](OPERATOR_AUTHORIZATION.md).

## Comportamiento ante presión de disco

En YELLOW, inspeccione el crecimiento y ejecute un plan en seco. En RED, ThreadCells puede admitir un arrendamiento pesado de Housekeeping seguro para recuperación aunque se pueda denegar el trabajo pesado ordinario. Los planes de presión ordenan primero los mayores candidatos demostrablemente seguros y muestran las clases protegidas dominantes, pero la limpieza sigue contando como una ejecución Heavy y no elude ninguna protección de candidatos.

YELLOW es un estado de inspección, no un permiso para fabricar bytes recuperables. Cuando todas las clases grandes restantes estén protegidas, cree capacidad externa o documente la superficie protegida en lugar de debilitar los criterios.

La recuperación de caché de paquetes se informa como desconocida/cero cuando el comando no puede demostrar los bytes; ThreadCells no anuncia recuperación estimada.

## Informes y fallo parcial

El informe más reciente registra la identidad del plan/ejecución, el estado de recursos, las estimaciones, los resultados reales, los resultados por candidato y códigos de motivo estables. El fallo de un candidato no debilita la protección de candidatos posteriores ni oculta éxitos independientes.

Después de una ejecución, verifique la presión de disco e inspeccione las entradas omitidas/fallidas. Vuelva a planificar antes de otra ejecución; no reutilice un plan antiguo tras cambios de estado.

## Copias de seguridad y releases

Las copias de seguridad son solo de inventario. Las decisiones de retención para el medio de backup pertenecen a la política de backup del operador, no a Housekeeping automático.

La limpieza de releases y candidatos comparte el bloqueo canónico de preparación y requiere metadatos de referencia de confianza. El Housekeeping normal protege los runtimes activo y de rollback. Full Cleanup protege solo la release activa y elimina intencionadamente todas las releases locales de rollback inactivas demostradas después de la confirmación explícita del operador. Consulte [Actualización](UPGRADING.md).

Los servicios programados de Housekeeping instalados reciben el grupo limitado de mantenimiento de releases necesario para recuperar un release inmutable elegible. El plano de control principal y los procesos ordinarios de agentes no lo reciben. Una ejecución manual/de API sin esa autoridad omite la eliminación de releases con `RELEASE_ADMIN_GROUP_REQUIRED`, continúa con la limpieza segura independiente y deja que el servicio programado recupere el release más tarde mediante el mismo motor de planificar/ejecutar.

La protección de rutas abiertas inventaría todos los procesos propiedad de la cuenta de runtime ThreadCells configurada, independientemente de qué cuenta autorizada invoque un plan manual. Las demás cuentas del host quedan fuera del límite de propiedad para el estado desechable de ThreadCells; las entradas privadas ilegibles de `/proc` de esas cuentas no deshabilitan la limpieza de todo el host. Una identidad de runtime desconocida o cualquier incertidumbre al inspeccionar un proceso de cuenta de runtime sigue fallando de forma cerrada.

## Errores habituales

- Eliminar directamente un directorio de worktree para recuperar espacio.
- Tratar un recuento estimado de bytes como recuperación garantizada.
- Ejecutar un plan que no se ha inspeccionado.
- Suponer que un PID detenido demuestra suficientemente que un grupo de navegador/proceso es el antiguo.
- Esperar que Housekeeping elimine las copias de seguridad.
- Elevar los umbrales de disco en vez de abordar el crecimiento sostenido.
