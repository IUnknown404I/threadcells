---
slug: resource-model
source: docs/RESOURCE_MODEL.md
source_sha256: sha256:50fdcf87c80a11bbd1e8d9c210e584f2640a388c71882bd4b2bf06af0b27f725
---

# Capacidad y modelo de recursos

ThreadCells separa la capacidad porque el trabajo de agentes de programación puede presionar partes distintas de un host en momentos diferentes. Un turno de modelo consume capacidad del proveedor; un contexto de programación asignado puede permanecer activo mientras el modelo está inactivo; una compilación puede saturar la máquina después de que se haya detenido la salida del modelo.

![Capacidad de orquestación activa que muestra límites independientes de residentes, proveedores, trabajo y ejecución pesada](/media/screenshots/threadcells-capacity.webp)

Aumentar todos los números a la vez normalmente no es más rápido. Puede generar contención de cuota de modelo, presión de memoria, actividad de disco y varias compilaciones costosas compitiendo por la misma CPU.

## Los cuatro límites

### Supervisores residentes

Una ranura residente mantiene una sesión de supervisor o propietario de nivel superior que debe seguir disponible a través de delegaciones y callbacks. Consume residencia incluso mientras espera el resultado de un trabajador.

Esto es independiente porque terminar un supervisor que parece inactivo puede perder el contexto responsable de integrar la misión.

### Ejecuciones de proveedor

Una ranura de ejecución de proveedor se usa mientras un modelo/proveedor produce activamente un turno. Las restricciones relevantes son la concurrencia del proveedor, la actividad de red, el número de procesos y, a veces, la memoria.

Un agente que espera en un prompt no necesita una ranura de ejecución de proveedor.

### Contextos de trabajo

Una ranura Work representa a un trabajador o revisor delegado que actualmente posee un contexto acotado. Puede mantener un worktree gestionado y autoridad de escritura mientras espera entre turnos de modelo.

La raíz de una sesión de nivel superior consume capacidad residente, no capacidad Work. Un hijo delegado residente consume capacidad Work.

### Ejecuciones pesadas

Una ranura Heavy es para trabajo intensivo del host, como una compilación de producción, una ejecución de Chromium, una suite de pruebas grande o un análisis de todo el repositorio. La admisión Heavy protege margen de CPU, memoria y E/S.

Usa el ejecutor pesado canónico para los comandos que cumplen los requisitos. Las pruebas pequeñas ordinarias y la inspección de archivos no necesitan una ranura Heavy.

## El punto de partida predeterminado

La configuración empaquetada `5 resident / 3 provider / 2 Work / 1 Heavy` es un punto de partida conservador para un host pequeño, no una referencia ni un límite fijo del producto.

Los intervalos permitidos son de 2 a 50 ranuras residentes y de 1 a 50 para cada uno de los demás límites. Los valores se conservan en la base de datos de runtime y tienen efecto sin reiniciar el servidor.

## ¿Qué debo establecer en mi máquina?

Empieza de forma conservadora, observa la presión de memoria/disco y las colas, y luego cambia un límite cada vez. Estos ejemplos ilustran la forma; no son garantías de rendimiento.

| Ejemplo de host | Residentes | Proveedor | Work | Heavy | Justificación |
| --- | ---: | ---: | ---: | ---: | --- |
| VPS pequeño | 2 | 1 | 1 | 1 | Un supervisor y un hijo acotado; serializa el trabajo costoso. |
| Estación de trabajo de desarrollador | 5 | 3 | 2 | 1 | Turnos de modelo paralelos útiles mientras las compilaciones permanecen serializadas. |
| Host compartido más grande | 8 | 5 | 4 | 2 | Más misiones y trabajadores residentes, con margen medido para dos tareas pesadas. |

Antes de aumentar un límite, pregunta qué cola está bloqueando realmente el progreso:

- Proveedor lleno pero CPU inactiva: considera una ranura Provider más si las cuotas lo permiten.
- Work lleno con capacidad de proveedor inactiva: retira hijos completados y acusados o aumenta Work con cautela.
- Heavy lleno durante compilaciones: una segunda ranura Heavy ayuda solo si CPU, RAM y disco pueden sostener compilaciones simultáneas.
- Resident lleno: cierra las sesiones de nivel superior completadas; no disfraces supervisores abandonados aumentando solo el límite.

## Presión de memoria y disco

ThreadCells observa la presión del host junto con los conteos configurados. Muchas CLI nativas, paneles tmux, procesos de navegador, worktrees, cachés de compilación y registros pueden sobrevivir al breve turno de proveedor que los creó.

El estado del disco usa umbrales exactos:

- **GREEN:** menos del 70 % usado.
- **YELLOW:** del 70 % a menos del 85 %.
- **RED:** del 85 % a menos del 92 %.
- **CRITICAL:** 92 % o más. La admisión agregada permanece en RED e incluye el
  motivo `DISK_CRITICAL`, mientras que la proyección específica de disco informa CRITICAL.

YELLOW es una indicación para inspeccionar el crecimiento y planificar Housekeeping. RED puede denegar trabajo nuevo arriesgado y admitir limpieza segura para recuperación. El estado desconocido falla de forma cerrada; ThreadCells no supone que un sistema de archivos ilegible esté saludable.

Una decisión explícita de Workflow Composer para un flujo de trabajo ya residente en una puerta del propietario también es una vía estrecha de recuperación cuando RED se debe únicamente al disco: consume capacidad normal de Provider, pero no crea un contexto Work. RED por memoria, PSI, motivos desconocidos o mixtos sigue fallando de forma cerrada; el turno duradero muestra un motivo de espera por recuperación de recursos sin consumir reintentos de transporte.

## Drenaje tras una reducción

Reducir un límite nunca mata trabajo activo. Si el uso actual está por encima del valor nuevo, esa categoría entra en **drenaje** y deniega nuevas admisiones hasta que el uso activo se sitúe dentro del límite.

Ejemplo: cambiar Work de 4 a 2 mientras hay tres hijos activos deja a los tres en ejecución. A medida que los hijos terminan y se retiran, no se admite ningún reemplazo hasta que el uso sea 2 o menos.

El inventario Heavy sigue contando ranuras activas con número mayor tras una reducción, por lo que un cambio de límite no puede ocultar un proceso costoso.

## Cuándo se libera la capacidad

- La capacidad del proveedor se libera cuando termina el turno activo del modelo.
- La capacidad Heavy se libera cuando sale el comando pesado registrado.
- La capacidad Work se libera solo después de que el contexto delegado se retire de forma segura.
- La capacidad residente se libera cuando se cierra la sesión de supervisor/propietario de nivel superior.

El resultado de un hijo completado debe registrarse, entregarse, incorporarse y acusarse antes de retirar recursos. El historial permanece después de liberar la capacidad de runtime.

La admisión vuelve a comprobarse en los límites de lanzamiento y continuación. Un turno de proveedor en cola empieza cuando hay una ranura de proveedor disponible. La finalización del proveedor solo libera capacidad de ejecución del proveedor; no cierra un flujo de trabajo abierto, descarta su callback ni libera un contexto Work delegado que todavía posee trabajo duradero.

## Configurar y observar

Usa Settings → Orchestration Capacity para ver el uso actual, los límites, las recomendaciones y el estado de drenaje. Los cambios de capacidad están protegidos por [Autorización del operador](OPERATOR_AUTHORIZATION.md) y se auditan.

La vista de estado de línea de comandos es:

```bash
threadcells-resource-status
```

Después de un cambio, verifica que la UI y la CLI estén de acuerdo. Un límite es un control de admisión, no una promesa de rendimiento ni un sandbox de carga de trabajo.

## Errores habituales

- Aumentar todos los límites porque una compilación es lenta.
- Contar un worktree inactivo como una ejecución de proveedor.
- Olvidar los supervisores residentes al dimensionar misiones de larga duración.
- Reducir un límite y esperar que se terminen las tareas activas.
- Tratar la capacidad GREEN como prueba de que las cuotas del proveedor están disponibles.
- Eliminar archivos de runtime para liberar una ranura en lugar de retirar de forma segura el flujo de trabajo propietario.

Consulta [Housekeeping](HOUSEKEEPING.md) para la recuperación de disco y [Flujos de trabajo y resultados duraderos](WORKFLOWS_AND_RESULTS.md) para el retiro seguro de hijos.
