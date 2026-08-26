---
slug: workflows-and-results
source: docs/WORKFLOWS_AND_RESULTS.md
source_sha256: sha256:2075858d138b70bafe8c6605e1d77b69a0aafee4faaa8042ef3437e6ebae71ff
---

# Flujos de trabajo y resultados duraderos

Un flujo de trabajo representa trabajo que debe permanecer coherente a través de varios turnos de modelo, terminales o agentes delegados. Impide que el mensaje final de un proveedor se confunda con la finalización de la misión más amplia.

![Sesión activa expandida de ThreadCells que muestra participantes del flujo de trabajo activos y completados](/media/screenshots/threadcells-session-workflow.webp)

## Trabajo de nivel superior y delegado

El **flujo de trabajo de nivel superior** pertenece al agente o supervisor lanzado para la misión del propietario. Un **flujo de trabajo delegado** pertenece a un hijo al que se asigna una tarea acotada.

```text
Top-level: "Prepare the release candidate"
  ├── Delegated: "Fix the statistics parser"
  ├── Delegated: "Review operator authorization"
  └── Owner gate: "Approve public publication"
```

Cada flujo de trabajo tiene su propia entrada lógica actual y estado de finalización. Un trabajador puede completar su flujo de trabajo delegado mientras el flujo de trabajo de nivel superior permanece abierto.

## Assign y handoff

**Assign** inicia trabajo acotado independiente y permite que el padre continúe. El resultado del hijo se entrega después. Es útil para investigación, implementación o revisión en paralelo.

**Handoff** transfiere una tarea acotada y espera su resultado validado antes de que el padre continúe. Es útil cuando el siguiente paso del padre depende directamente de esa respuesta.

Ambas formas preservan la identidad padre/hijo y un resultado duradero. Ninguna concede a un hijo una autoridad de propietario mayor que la que el padre delegó explícitamente.

Una denegación transitoria de admisión previa al lanzamiento, como capacidad agotada de contexto de trabajo, se registra como no admitida y no como una asignación ejecutada. El mismo efecto lógico puede reintentarse una vez que haya capacidad disponible; después de que el lanzamiento de un hijo sea admitido o su resultado se vuelva incierto, se mantiene la protección normal contra duplicados.

## Ciclo de vida del resultado

```text
Task admitted
   ↓
Child works
   ↓
Structured result recorded
   ↓
Result delivered to parent
   ↓
Parent reads and incorporates it
   ↓
Parent acknowledges incorporation
   ↓
Eligible child resources can retire
```

Normalmente, un resultado incluye un resumen conciso, archivos modificados, comprobaciones realizadas, riesgos restantes y bloqueadores. Es evidencia operativa, no un sustituto de examinar el diff o la salida de las pruebas.

La entrega es de al menos una vez. Si el padre se reinicia antes de acusar un resultado entregado, ThreadCells puede entregarlo otra vez. El padre debería usar la identidad inmutable del resultado para evitar incorporar el mismo trabajo dos veces.

La entrega de Inbox sigue FIFO dentro de un terminal y queda vinculada al flujo de trabajo y al turno lógico exactos que la crearon. Un transporte pendiente es estado de entrega, no autoridad para mover un payload o resultado a otro flujo de trabajo. Si el flujo de trabajo asociado ya no está abierto, ThreadCells terminaliza ese transporte obsoleto y permite que continúe el trabajo más reciente del propietario en un flujo de trabajo abierto, sin volver a vincular las identidades de payload, flujo de trabajo, entrega, receipt o effect. La misma reconciliación se ejecuta después de reiniciar y es idempotente.

## Finalización del proveedor frente a finalización del flujo de trabajo

Un turno de proveedor termina cuando el modelo devuelve el control. La misión puede seguir teniendo trabajo elegible: otra prueba, un hijo pendiente, una pasada de corrección o un paso de despliegue.

Por ello, ThreadCells mantiene abierto un flujo de trabajo de nivel superior hasta que se produce uno de estos resultados explícitos:

- la misión autorizada por el propietario está completa;
- realmente se requiere una puerta del propietario;
- el propietario la cancela;
- un fallo real no recuperable agota su ruta de recuperación acotada.

Los mensajes finales ordinarios repetidos del proveedor usan una continuación duradera de un turno cada vez con backoff limitado. ThreadCells sigue admitiendo el siguiente turno lógico mientras el flujo de trabajo esté abierto. Si un proveedor se asienta directamente en Ready en lugar de exponer un marco completado repetible, ThreadCells aplica un rebote duradero a ese estado tras reiniciar y avanza el mismo flujo de trabajo abierto; una observación posterior de Processing cancela un candidato Ready transitorio. La entrada directa del propietario y los resultados duraderos de hijos restablecen el contador de falta de progreso. Como salvaguarda frente a bucles de pago, 65 finales consecutivos sin progreso duradero colocan el flujo de trabajo en una puerta explícita y visible para el propietario. La finalización del proveedor nunca se convierte en finalización de la misión, y la continuación autónoma normal no requiere despertar al propietario.

## Puertas del propietario

Usa una puerta del propietario cuando el siguiente paso necesite autoridad que la misión no otorgó. Buenos ejemplos incluyen publicar en un remoto público, exponer un nuevo servicio de red, pagar un recurso o elegir entre semánticas de producto materialmente distintas.

No uses una puerta del propietario simplemente porque el trabajo sea lento, falle una prueba o termine un turno de proveedor. Continúa primero cualquier trabajo independiente elegible.

Un mensaje nuevo en Workflow Composer es la decisión del propietario que reanuda un flujo de trabajo residente elegible. ThreadCells conserva la procedencia de la puerta del propietario y admite exactamente una vez el turno duradero. Si la solicitud ya estaba en cola cuando su predecesor agotó los reintentos de transporte, el turno del propietario se promueve atómicamente en vez de quedar oculto bajo otra puerta. Una denegación transitoria de capacidad o de política de recursos deja un motivo de espera duradero y explícito, sin consumir el presupuesto de fallos de transporte.

## Recuperación

Al reiniciar, ThreadCells reconstruye la propiedad del flujo de trabajo desde el estado duradero. Los resultados entregados pero no acusados siguen disponibles. Un handoff en espera puede reanudarse con el mismo hijo en vez de lanzar un duplicado. Una vez que se admite un turno lógico más nuevo para un flujo de trabajo abierto, una continuación pendiente más antigua queda sustituida de forma duradera y no puede reproducirse después como trabajo independiente tras una compactación o interrupción.

La reconciliación al reiniciar también vuelve a abrir el flujo de trabajo más reciente en puerta del propietario cuando su cabecera de transporte cancelada ya tiene un sucesor explícito posterior de Composer. Promueve ese sucesor existente sin crear un turno de reemplazo y nunca usa esta reparación para revivir callbacks de Inbox ni terminales Exited.

Si la ejecución del proveedor o del modelo se interrumpe después de admitir su entrada lógica pero antes de terminar el trabajo requerido, ThreadCells la reanuda mediante un nuevo turno de continuación duradero en lugar de reproducir el receipt original. Los efectos completados siguen cercados, la propiedad de ejecución del proveedor acompaña al turno reanudado y el mismo resultado inmutable del hijo y la barrera de finalización siguen disponibles para incorporarlos y confirmarlos exactamente una vez.

La finalización directa, el fallo, la cancelación, la puerta del propietario, la terminalización de hijos y la cancelación central de flujos de trabajo protegidos cercan los transportes pendientes de Inbox en la misma transacción de base de datos. Así se evita que una transición terminal deje un estado de entrega ordinario capaz de bloquear un turno posterior del propietario.

Un reinicio del servicio con la misma compilación mantiene compatible la conexión de control del lado del proveedor. Después de que una compilación promovida cambie el código de orquestación privilegiado, una conexión antigua queda cercada antes de que pueda crear un efecto. Si la identidad activa no está disponible temporalmente durante el reinicio, la operación se rechaza sin efecto y se reintenta cuando el servicio vuelve. Para Codex, ThreadCells vincula la conversación exacta del proveedor al terminal gestionado y a la generación de runtime al estar listo el lanzamiento, y después conserva esa identidad como autoridad de reconexión. Otros archivos de despliegue abiertos no pueden volver ambiguo ese terminal gestionado. Una identidad ausente, obsoleta, incorrecta o imposible de probar falla de forma cerrada antes del despacho al proveedor. La identidad de reanudación duradera hace seguro un reinicio del servicio incluso entre la salida y el relanzamiento. El transporte de entrada, la reconexión y el retiro comparten una única reclamación de mutación duradera por terminal, por lo que no se puede pegar texto en el hueco de shell de reconexión y una reconexión obsoleta no puede relanzarse después de que gane el retiro. El turno lógico ya duradero se reintenta en lugar de reemplazarse.

Si un terminal desaparece, inspecciona los registros de flujo de trabajo y de resultados antes de reintentar. Un terminal nuevo no debe duplicar silenciosamente una mutación que el anterior ya completó.

## Ejemplo concreto

1. El propietario lanza un supervisor para añadir una función y validarla.
2. El supervisor asigna la implementación a un desarrollador y sigue inspeccionando las pruebas.
3. El desarrollador confirma el cambio y registra un resultado.
4. ThreadCells lo entrega; el supervisor lee el diff y acusa su incorporación.
5. El supervisor asigna un revisor independiente.
6. El revisor encuentra una regresión de navegador bloqueante y registra evidencia.
7. El supervisor continúa el mismo flujo de trabajo de nivel superior abierto, solicita una corrección y vuelve a ejecutar la aceptación.
8. Solo después de la compilación aceptada y el despliegue autorizado el supervisor completa explícitamente el flujo de trabajo.

En los pasos 3, 4 y 6, han terminado turnos de modelo individuales. La misión no.

## Errores habituales

- Tratar un mensaje final del terminal como la finalización de nivel superior.
- Acusar un resultado antes de leerlo o usarlo.
- Lanzar un hijo de reemplazo sin comprobar un resultado duradero anterior.
- Permitir que dos hijos modifiquen el mismo worktree.
- Usar una puerta del propietario como botón de pausa genérico.

Consulta [Proyectos y worktrees gestionados](PROJECTS_AND_WORKTREES.md) para el aislamiento de escritura y [Capacidad y modelo de recursos](RESOURCE_MODEL.md) para los límites de admisión.
