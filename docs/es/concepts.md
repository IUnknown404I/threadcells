---
slug: concepts
source: docs/CONCEPTS.md
source_sha256: sha256:558a270183e49568ee5d52d6efd30c6a8215fe7d563df596ee9391313d8f3299
---

# Conceptos básicos

ThreadCells añade estructura alrededor de terminales nativos de agentes de programación. Esta página presenta una idea cada vez y luego muestra cómo encajan las piezas.

## Agente

Un **agente** es una CLI de proveedor que se ejecuta con un prompt, rol, perfil y contexto de proyecto. Puede inspeccionar archivos, usar herramientas, escribir código cuando está autorizado y devolver un resultado.

Un agente no es solo el nombre del modelo. Dos agentes pueden usar el mismo modelo y tener roles, permisos, ajustes de razonamiento y worktrees distintos.

## Terminal

Un **terminal** es el entorno de proceso real respaldado por tmux en el que se ejecuta un agente. Conserva la salida nativa del proveedor y permite al operador reconectar después de cerrar el navegador.

El terminal puede salir mientras permanece su resultado duradero. A la inversa, que siga existiendo un terminal no prueba que el trabajo útil continúe avanzando.

## Sesión

Una **sesión** es la vida útil duradera de ThreadCells para un grupo relacionado de ejecuciones de agentes: identidad, ciclo de vida, terminales, proveedores, perfiles, proyecto, uso y relaciones de resultados. **Add Agent** añade un terminal a esa vida útil de sesión exacta en vez de inferir membresía a partir de un nombre mostrado reutilizado. Las sesiones permiten que Statistics y los flujos de trabajo razonen sobre ejecuciones activas, completadas, históricas o retenidas.

## Proyecto

Un **proyecto** identifica la autoridad canónica de Git y del código fuente para el trabajo. Da a ThreadCells un ámbito estable para sesiones, worktrees y resultados; la raíz de fuentes registrada no es el cwd escribible normal de un supervisor nuevo ni sustituye a los remotos de Git o a los permisos del repositorio.

## Worktree gestionado

Un **worktree gestionado** es un Git worktree creado para un contexto escribible acotado. Cada nueva Sesión de supervisor asociada a un Proyecto, incluida la primera, recibe uno. Las Sesiones independientes del mismo Proyecto usan ramas y checkouts distintos; un recovery takeover del mismo contexto conserva su worktree existente.

Los worktrees reducen las colisiones; no son sandboxes de seguridad. Un agente puede seguir alcanzando todo lo que pueda alcanzar su cuenta del sistema operativo.

## Autoridad de escritura

La **autoridad de escritura** responde quién puede modificar un contexto de trabajo concreto. ThreadCells mantiene explícita esa propiedad para que dos agentes activos de forma independiente no se consideren por accidente escritores concurrentes seguros del mismo worktree.

Un revisor suele necesitar acceso de lectura, pero no autoridad de escritura. Un desarrollador que realiza una implementación sí la necesita.

## Proveedor

Un **proveedor** conecta ThreadCells con una CLI nativa de agente de programación como Codex o Claude Code. Importan tres estados:

1. ThreadCells contiene un adaptador de proveedor.
2. La CLI correspondiente está instalada para el usuario de runtime.
3. Esa CLI está en buen estado y suficientemente autenticada para iniciar.

Que un adaptador aparezca en Settings no implica que la CLI externa esté instalada. Consulta [Proveedores](PROVIDERS.md).

## Perfil

Un **perfil** es una política de inicio reutilizable. Selecciona proveedor/modelo y nivel de razonamiento, proporciona instrucciones y capacidades, define un rol y puede restringir cómo participa un agente en la orquestación.

Los perfiles integrados ofrecen roles conocidos y seguros. Los perfiles personalizados permiten a los operadores adaptar esos roles sin cambiar el código de la aplicación.

## Supervisor y trabajador

Un **supervisor** posee una misión más amplia. Puede dividirla en tareas acotadas, enviarlas a trabajadores, recoger sus resultados duraderos, solicitar revisión y decidir cuándo la misión está realmente completa.

Un **trabajador** o **agente delegado** posee una de esas tareas acotadas. Un trabajador debe informar de su evidencia a su padre; no decide en silencio el resultado de nivel superior.

```text
Owner
  ↓
Supervisor
  ├── Developer ── implementation result ──┐
  └── Reviewer  ── acceptance result ──────┤
                                           ↓
                              Supervisor incorporates results
                                           ↓
                                  Top-level completion
```

Un **supervisor residente** puede permanecer disponible mientras los trabajadores realizan turnos. Su residencia consume una plaza de supervisor incluso cuando el modelo no está produciendo salida.

## Flujo de trabajo

Un **flujo de trabajo** es el registro duradero de coordinación de una misión o tarea delegada. Realiza seguimiento de quién posee el trabajo, qué entrada lógica es actual, si los resultados se entregaron e incorporaron y si se requiere finalización o una decisión del propietario.

La finalización de un turno de proveedor/modelo no es la finalización del flujo de trabajo. Un supervisor puede terminar un turno, recibir más tarde un resultado de trabajador y continuar la misma misión abierta.

## Resultado duradero

Un **resultado duradero** es la evidencia estructurada de finalización producida por trabajo delegado. Puede incluir un resumen, archivos modificados, comprobaciones, riesgos y bloqueadores. ThreadCells lo almacena y entrega incluso si el terminal del trabajador se retira después.

La entrega no es lo mismo que la incorporación. El supervisor reconoce un resultado solo después de haberlo usado o evaluado realmente.

## Control de propietario

Un **control de propietario** pausa la continuación autónoma porque la siguiente decisión requiere al propietario humano; por ejemplo, una publicación, un nuevo límite de confianza externo, una acción destructiva irreversible o una decisión de producto que no estaba autorizada previamente.

Que termine un turno de modelo ordinario o haya un paso de implementación difícil no constituye un control de propietario.

## Cuatro tipos de capacidad

ThreadCells separa cuatro límites de capacidad porque restringen partes diferentes de la máquina.

### Supervisor residente

Un supervisor o propietario de nivel superior permanece disponible para recibir callbacks y continuar su flujo de trabajo. La residencia es distinta de la ejecución activa del modelo y de la capacidad de trabajo delegada.

### Ejecución de proveedor

El modelo está produciendo activamente un turno. Las cuotas de proveedor, límites de proceso y actividad de red restringen esta categoría.

### Contexto de trabajo

Un contexto de programación delegado posee trabajo actualmente. Puede retener un worktree y autoridad de escritura incluso mientras espera un comando o callback.

### Ejecución pesada

Una compilación, ejecución de Chromium, suite de pruebas grande o tarea de host similarmente costosa ocupa una plaza pesada. La presión de CPU, memoria y E/S restringe esta categoría.

Un supervisor residente puede esperar sin usar una plaza de proveedor, y un agente delegado puede mantener un contexto de trabajo sin usar una plaza de proveedor ni pesada. Por tanto, subir todos los límites a la vez puede sobrecargar el host sin acelerar el flujo de trabajo. Consulta [Capacidad y modelo de recursos](RESOURCE_MODEL.md).

## Un ejemplo completo

Un propietario inicia un supervisor para un repositorio. El supervisor asigna a un desarrollador un worktree gestionado y autoridad de escritura. El desarrollador usa una ejecución de proveedor al generar código y después una plaza pesada para la compilación de producción. Su resultado duradero vuelve al supervisor. Un revisor lee el worktree e informa una regresión bloqueante. El supervisor inicia otro turno, pide al desarrollador corregirla, incorpora ambos resultados y completa explícitamente el flujo de trabajo.

El terminal, la sesión, el worktree, el flujo de trabajo y el resultado son distintos porque cada uno tiene una vida útil y una verdad diferentes que preservar.

Siguiente: [Flujos de trabajo y resultados duraderos](WORKFLOWS_AND_RESULTS.md) convierte este vocabulario en un tutorial operativo.
