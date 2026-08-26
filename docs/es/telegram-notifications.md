---
slug: telegram-notifications
source: docs/TELEGRAM_NOTIFICATIONS.md
source_sha256: sha256:c1c50ae5d9e7937dff2794e49e2914929e7d02e4adb3de0c2f04c7cd5d656735
---

# Notificaciones de Telegram

ThreadCells puede enviar notificaciones de ciclo de vida de bajo ruido a un destino de Telegram. Es una capacidad de ThreadCells global de la instalación: no pertenece al proyecto seleccionado actualmente, no lee su configuración de él ni depende de este.

![Ajustes activos de notificaciones de Telegram con los campos de destino y credenciales explícitamente censurados](/media/screenshots/threadcells-telegram.webp)

## Configurar el destino

1. Crea o elige un bot de Telegram mediante el flujo admitido de gestión de bots de Telegram.
2. Obtén el ID de chat de destino. Para un tema de foro, obtén también su ID de hilo de mensajes positivo.
3. Abre **Settings → Telegram** y desbloquea los cambios de operador.
4. Introduce el token del bot, el ID de chat y el ID opcional de tema/hilo.
5. Guarda mientras las notificaciones están deshabilitadas.
6. Usa **Check connection** para validar la credencial del bot; después, **Send test notification** para validar el destino.
7. Habilita las notificaciones y guarda de nuevo.

La acción de prueba es explícita; abrir Settings nunca contacta a Telegram. Deshabilitar las notificaciones conserva el destino y el token configurados para que se puedan volver a habilitar más tarde. **Clear bot token** es una acción de operador confirmada e independiente: elimina la credencial, deshabilita las notificaciones y conserva los campos de destino no secretos.

## Manejo de secretos

La UI Web envía un token nuevo solo en una actualización protegida y borra después su campo de contraseña. Las API de lectura informan solo `Configured`, `Not configured` o `Invalid`; nunca devuelven el token. ThreadCells no guarda el token en el almacenamiento del navegador, prompts de terminal, metadatos de sesión o agente, registros normales ni la fila de ajustes SQLite.

El servidor almacena el token en:

```text
$CAO_HOME_DIR/secrets/telegram-bot-token
```

El directorio padre está restringido a la cuenta de runtime y el archivo de token usa el modo `0600`. La sustitución usa un renombrado atómico del sistema de archivos; la limpieza desvincula la credencial sin seguirla y sincroniza el directorio de secretos. `CAO_HOME_DIR` es la raíz de estado mutable privada de la instalación, no una ruta pública del repositorio.

Trata este archivo como una credencial. No lo copies al control de código fuente, paquetes de soporte ordinarios, exportaciones de base de datos, historial de shell ni capturas de pantalla. Rótalo mediante Telegram si sospechas una divulgación.

## Política de notificaciones

La política de primer lanzamiento envía como máximo un intento por cada evento duradero de flujo de trabajo de nivel superior:

- finalización correcta de nivel superior;
- una puerta de atención del propietario de nivel superior;
- fallo inesperado de un terminal de nivel superior mientras su flujo de trabajo está abierto.

ThreadCells no notifica por finalización de hijos, delegación, sondeo, actualizaciones de progreso, ciclos de reintento internos ni cada turno de modelo/herramienta. Las claves de evento duraderas impiden que una observación repetida o un reinicio dupliquen una entrega ya reclamada.

Los mensajes contienen solo contexto seguro y conciso: identidad de ThreadCells, sesión, nombre mostrado del proyecto cuando está presente, estado de ciclo de vida, un resumen fijo y marca de tiempo UTC. No incluyen prompts, salida de modelo, volcados de sistema de archivos, cuerpos de excepciones, secretos de operador ni el token del bot.

## Comportamiento ante fallos

La entrega de Telegram es abierta ante fallos para el trabajo de agentes. Un timeout, una credencial rechazada o un servicio de Telegram no disponible registra un código de resultado seguro, pero no puede fallar ni reabrir el flujo de trabajo. La entrega tiene un único intento acotado; ThreadCells no reintenta sin fin ni reproduce eventos históricos después de habilitar las notificaciones.

**Check connection** valida el token del bot con Telegram. **Send test notification** también valida el enrutamiento de chat/tema configurado. Una comprobación de conexión correcta no demuestra que el bot pueda escribir en el destino elegido, así que usa ambas acciones al configurar un destino nuevo.

## Copia de seguridad y restauración

El estado no secreto de habilitación/destino y el libro de entrega están en la base de datos de ThreadCells. El token del bot está separado. Si las notificaciones deben sobrevivir a una recuperación ante desastres, respalda el token como credencial cifrada independiente con propiedad y modo preservados; no lo añadas a un archivo rutinario de base de datos en texto plano.

Después de restaurar, verifica la ruta y los permisos del secreto, deja las notificaciones deshabilitadas inicialmente, ejecuta ambas comprobaciones explícitas y después habilita la entrega. Restaurar la base de datos sin el token informa de forma segura `Not configured`.

## Solución de problemas

- **No configurado:** proporciona tanto un token de bot válido como un ID de chat antes de habilitar.
- **Almacenamiento de token no válido:** comprueba que el token es un archivo normal, no simbólico, propiedad de la cuenta de runtime y sin permisos para grupo u otros.
- **Falló la conexión:** comprueba HTTPS/DNS saliente y rota o sustituye un token de bot rechazado; los errores seguros de la UI omiten deliberadamente los detalles de respuesta de Telegram.
- **La conexión funciona, pero falla la prueba:** confirma que el bot pertenece al destino y puede publicar allí; comprueba los ID de chat y, si corresponde, de tema.
- **No llega un mensaje de ciclo de vida:** confirma que Enabled está activado y recuerda que solo se notifican la finalización de nivel superior, la atención del propietario y el fallo inesperado de nivel superior. Los eventos ocurridos mientras estaba deshabilitado no se reproducen.
