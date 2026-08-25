---
slug: troubleshooting
source: docs/TROUBLESHOOTING.md
source_sha256: sha256:5e66928bd64c8837b5480d71160eb1548dbe9369be3069800fcf18e0f1e99836
---

# Solución de problemas

Empiece por conservar las evidencias: identidad actual de la compilación, texto de error seguro, sesión/flujo de trabajo afectado, estado de capacidad, registros recientes y estado Git. Evite la limpieza, eliminación o reintentos a ciegas hasta saber si una operación duradera ya tuvo éxito.

## La UI web no se inicia

**Comprobaciones:** ejecute el servidor en primer plano, llame a `curl -fsS http://127.0.0.1:9889/health`, confirme que el puerto escucha en loopback e inspeccione Settings → About cuando esté disponible.

**Resolución:** corrija el error informado de dependencia/configuración o el conflicto de puerto. Si health funciona pero no los archivos estáticos, verifique el candidato y asegúrese de que el código Python y los activos Web empaquetados proceden de la misma compilación.

## El navegador en otra máquina no se puede conectar

Esto es lo esperado cuando ThreadCells escucha correctamente en loopback. No lo cambie a un bind público. Use el túnel SSH o el proxy autenticado de [Acceso remoto](REMOTE_ACCESS.md).

## El proveedor muestra que la CLI no está instalada

**Comprobaciones:** compare Settings → Providers con Spawn Agent y después ejecute `command -v PROVIDER_COMMAND` como el usuario de runtime de ThreadCells.

**Resolución:** instale la CLI canónica del proveedor para esa cuenta, corrija su `PATH` de servicio o elija otro proveedor listo. El registro del adaptador por sí solo no es una instalación.

## El proveedor está instalado pero no autenticado

**Comprobaciones:** ejecute el comando de estado de autenticación compatible del proveedor como el usuario de runtime.

**Resolución:** complete el flujo de inicio de sesión nativo del proveedor. ThreadCells no copia las credenciales de otro usuario ni inicia sesión durante el preflight.

## El proveedor indica que la disponibilidad no está verificada

El comando existe, pero no puede exponer de forma segura la realidad de autenticación no interactiva. Verifique su versión y realice una pequeña prueba nativa. Puede seguir siendo iniciable; inspeccione el terminal resultante en busca de un prompt de inicio de sesión del proveedor.

## El agente no se inicia

**Comprobaciones:** disponibilidad del proveedor, vista previa resuelta del perfil seleccionado, ruta/permisos del proyecto, capacidad residente/Provider/Work, disponibilidad de tmux y salida de inicio del terminal.

**Resolución:** corrija la primera admisión fallida o el prerrequisito de proveedor. No inicie duplicados repetidamente mientras una primera sesión todavía se esté iniciando.

## Capacidad agotada

Abra Orchestration Capacity e identifique la categoría exacta llena. Retire trabajo completado de forma segura o espere la tarea de proveedor/pesada correspondiente. Aumente solo ese límite cuando el host y la cuota tengan margen medido.

## Ranura de ejecución pesada no disponible

Una compilación, prueba de navegador, análisis o trabajo de recuperación mantiene la ranura Heavy. Espere a que termine o investigue un arrendamiento obsoleto mediante el estado canónico. No ejecute un comando costoso fuera de la admisión solo para eludir la cola.

## Flujo de trabajo esperando al propietario

Lea el motivo de la puerta. Proporcione la decisión solicitada solo si se trata de un límite real de publicación, confianza, destrucción, coste o semántica de producto. Un mensaje final ordinario de proveedor debe dejar abierto el trabajo autónomo elegible; informe un cierre automático como defecto del flujo de trabajo.

## Resultado no incorporado

Confirme que el hijo registró un resultado duradero y que se entregó al padre correcto. El padre debe leer/usar el resultado inmutable y después confirmar la incorporación. La repetición tras reinicio puede volver a entregar un resultado no confirmado; no lo aplique dos veces.

## La nueva entrada del propietario permanece en cola detrás de un flujo de trabajo cerrado

Reinicie una vez el runtime mediante el procedimiento compatible e inspeccione las identidades exactas del flujo de trabajo y de Inbox. Las versiones actuales reconcilian un transporte ordinario de Inbox pendiente cuyo flujo de trabajo asociado ya no está abierto, y después permiten que continúe el turno más reciente del propietario en un flujo de trabajo abierto. No vuelva a asociar ni edite manualmente la fila de Inbox; conserve la base de datos e informe de un defecto si el transporte obsoleto sigue pendiente o si algún payload cruza la identidad del flujo de trabajo.

## Autorización de operador no configurada

Confirme que `THREADCELLS_OPERATOR_VERIFIER_FILE` llega al proceso real del servidor y reinicie. Si la configuración no es válida, compruebe el esquema, la ruta absoluta/canónica, el propietario/modo del archivo, la legibilidad y cada directorio padre. La cuenta de servicio no debe poseer ni poder reemplazar el verificador.

## El secreto de operador correcto falla

Confirme que el servidor cargó el mismo verificador que generó la CLI. El mínimo es exactamente cinco caracteres. Compruebe si hay un proceso de servidor antiguo o un verificador reemplazado recientemente; no registre el secreto introducido.

## Telegram no está configurado o falla una prueba

Abra Settings → Telegram después de desbloquear los cambios de operador. `Not configured` requiere tanto un token de bot válido como un ID de chat. `Invalid` significa que el archivo de token privado no superó sus comprobaciones de propiedad, archivo regular o modo. Una comprobación de conexión correcta valida la credencial del bot; envíe una notificación de prueba explícita para validar el chat y el ID de tema opcional. Compruebe HTTPS/DNS salientes si alguna acción falla. Los errores seguros omiten intencionadamente los cuerpos de respuesta de Telegram y el token. Consulte [Notificaciones de Telegram](TELEGRAM_NOTIFICATIONS.md).

## Statistics no incluye una sesión actual

Actualice uso/estado, verifique que el proveedor admite telemetría y confirme que su evidencia de despliegue duradera sigue siendo legible. No es necesario eliminar sesiones antes de contarlas. Los campos ausentes de proveedor deben indicar Not reported, no cero.

## El total de Statistics parece duplicado

Compare las dimensiones global, de sesión y de terminal y conserve la base de datos. Los snapshots acumulativos del proveedor deben actualizar un punto de control estable entre sondeo/reinicio/repetición. No elimine filas manualmente antes del diagnóstico.

## Incompatibilidad entre Docs e identidad de compilación

Settings → About, el pie de página de Docs, el manifiesto del candidato y la revisión de activos estáticos deben coincidir. Reconstruya y verifique un candidato inmutable; no combine salida Web de un checkout con código Python de otro.

## Presión de disco o Housekeeping no puede recuperar

Inspeccione un plan dry-run de Housekeeping. Los elementos protegidos, activos, desconocidos, de backup, actuales y de rollback se conservan intencionadamente. Aborde la referencia/propietario informado o amplíe el disco de forma segura; nunca elimine recursivamente la raíz de runtime.

## El terminal del navegador no se reconecta después de reiniciar

Actualice una vez, confirme que el servidor y la sesión tmux estén en buen estado y compruebe la conexión WebSocket del navegador a través de cualquier proxy inverso. Asegúrese de que Caddy u otro proxy no elimina los encabezados de upgrade. Una PWA instalada no almacena en caché el estado de terminal ni de WebSocket.

## Sigue bloqueado

Conserve la menor evidencia reproducible y ejecute las comprobaciones de componentes enfocadas antes de suites amplias. Incluya solo rutas y mensajes seguros para el público en informes de incidencias. Consulte [Contribuir](../CONTRIBUTING.md) para conocer las expectativas de informe.
