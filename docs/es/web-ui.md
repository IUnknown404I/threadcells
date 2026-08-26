---
slug: web-ui
source: docs/WEB_UI.md
source_sha256: sha256:dc45952406ae34d9be16d78c4e5b4a6f73d8862fe8b5fe6a557528cecaf45928
---

# Uso de la Web UI

La Web UI es la vista en vivo del operador sobre ThreadCells. Está diseñada para un listener loopback y funciona normalmente en un navegador o como PWA básica instalada. Instalarla no añade comportamiento operativo sin conexión ni un nuevo límite de autenticación.

![Inicio en vivo de ThreadCells con resúmenes densos de sesiones, agentes y flujos de trabajo](/media/screenshots/threadcells-home.webp)

## Áreas principales

- **Home** resume el historial duradero de sesiones y agentes, la actividad actual, la atención del propietario y los recuentos de estado First/Last/Total sin cargar todos los terminales.
- **Agents** ofrece vistas de Sessions, Statuses y Profiles sobre terminales, identidad de perfil/proveedor, estado de ejecución, estado de flujo de trabajo y resultados duraderos.
- **Flows** crea, habilita, deshabilita, inspecciona y ejecuta manualmente programaciones recurrentes de agentes. Los agentes resultantes y el ciclo de vida del flujo de trabajo aparecen en Agents.
- **Statistics** muestra el uso informado por los proveedores sin métricas inventadas.
- **Settings** contiene General, Orchestration Capacity, Profiles, Providers, Housekeeping, notificaciones de Telegram globales de la instalación y About.
- **Docs** sirve la documentación pública permitida empaquetada con la compilación en ejecución.
- **Spawn Agent** inicia una sesión nueva a partir de un proyecto, proveedor y perfil.
- **Add Agent** inicia otro terminal dentro de la vida útil exacta de la sesión seleccionada; no se une a otra sesión histórica que por casualidad tenga el mismo nombre.

Se admiten URL directas. El historial del navegador debe conservar la página seleccionada de Settings y Docs.

## Un ciclo operativo normal

1. Comprueba Home para ver la actividad actual de sesión/flujo de trabajo y Settings para conocer la salud del host, presión de disco y capacidad disponible.
2. Usa Spawn Agent y confirma que el proveedor seleccionado está listo.
3. Observa la nueva sesión en Agents.
4. Usa Flows para programaciones recurrentes. Sigue en Agents a los agentes que inician.
5. Lee e incorpora los resultados duraderos antes de retirar hijos.
6. Usa Statistics para comprender el uso informado por el proveedor.

Las etiquetas de estado proceden de la verdad duradera del plano de control. **Processing** significa que un turno está activo; **Ready** significa que el runtime del proveedor está vivo y realmente inactivo. Las etiquetas de cola distinguen agotamiento de capacidad del proveedor, barreras de retirada de hijos y continuación general del flujo de trabajo. Una insignia de control de propietario permanece categórica, mientras que el panel ampliado Owner Decision muestra el motivo duradero concreto.

Las sesiones activas e históricas siguen siendo vidas útiles duraderas separadas. Eliminar una sesión histórica elimina solo esa vida útil exacta elegible. Eliminar un terminal salido también comprueba su identidad de runtime exacta, lease de escritor, protección de flujo de trabajo/resultado y relación de sesión antes de la limpieza; los estados ambiguos o activos permanecen protegidos. Los recursos de limpieza retenidos no crean un falso bloqueo de ejecución: la vida útil exacta se puede marcar con una lápida mientras la autoridad protegida sobre el sistema de archivos sigue disponible para su retirada posterior, y repetir la misma eliminación es seguro.

Los agentes de una sesión siempre usan su secuencia de creación duradera. Home y Agents conservan el mismo orden en List y Grid, durante la expansión, el sondeo, la reconexión, el reinicio y los cambios del ciclo de vida. El estado, el ID, el proveedor, el perfil, la actividad y la hora de actualización no son claves de ordenación de presentación; un agente nuevo se añade al final de la sesión.

![Vista de estado de Agents en vivo con rutas locales de worktree eliminadas de la captura pública](/media/screenshots/threadcells-agents.webp)

## Configuración protegida

Las mutaciones sensibles comparten un control **Unlock operator changes**. Los estados ausente, inválido, bloqueado, desbloqueado y expirado son distintos. La longitud mínima exacta del secreto es de cinco caracteres y la sesión autenticada predeterminada dura cinco minutos.

La UI envía el secreto solo para desbloquearlo, lo borra de inmediato y nunca lo coloca en persistencia del navegador ni exportaciones. La capacidad, cambios privilegiados de perfil/proveedor, configuración/pruebas de Telegram, ejecución de Housekeeping, ejecución de Full Cleanup e inicios de propietario aplicables permanecen bloqueados sin la sesión de servidor.

Settings → Housekeeping termina con el bloque de peligro **Delete all system files — Full Cleanup**. Su vista previa de solo lectura muestra estimaciones de recuperación por clase, motivos de protección, estado de inactividad, releases/worktrees y la advertencia de que solo permanecerá la release activa. El modal de confirmación existente es obligatorio después del desbloqueo. La ejecución se deshabilita mientras algún agente o ejecución que modifique el sistema de archivos esté activo, y el servidor vuelve a comprobar esa condición antes de eliminar. El resultado informa de la recuperación planificada/real, los elementos omitidos, el estado del disco, la release activa y la disponibilidad de rollback.

Sigue [Autorización del operador](OPERATOR_AUTHORIZATION.md) para aprovisionar el verificador de forma segura.

## Selección de proveedor y perfil

Las etiquetas de proveedor distinguen **Built-in adapter** de **CLI ready**, **CLI not installed**, **Authentication required**, **Installed but unhealthy** o **Readiness unverified**. Spawn deshabilita solo un proveedor cuya falta de disponibilidad esté demostrada y usa la misma comprobación previa del servidor que Settings.

Los perfiles priorizan descubrimiento integrado/personalizado con búsqueda y vistas previas resueltas. La importación/exportación de artefactos sin procesar está intencionadamente en Advanced. Seleccionar el perfil excepcional de propietario XHigh muestra una advertencia de autoridad y exige su ruta de concesión independiente.

## Notificaciones de Telegram

Settings → Telegram configura un único destino global de la instalación, independiente de los proyectos. El token de bot es solo de escritura en la UI; las acciones de conexión y mensaje de prueba son explícitas, y la acción de borrado confirmada e independiente deshabilita la entrega y elimina la credencial. La entrega habilitada cubre solo finalización de nivel superior, controles que requieren atención del propietario y fallos inesperados del terminal de nivel superior, con supresión duradera de duplicados y entrega fail-open. Consulta [Notificaciones de Telegram](TELEGRAM_NOTIFICATIONS.md).

## Statistics

Statistics incluye sesiones activas, completadas y retenidas no eliminadas en cuanto la telemetría duradera del proveedor está disponible. La entrada en caché y la salida de razonamiento siguen separadas; los campos no disponibles muestran **Not reported**. Consulta [Estadísticas y uso de proveedor](STATISTICS.md).

## Lector de Docs

La navegación de Docs se agrupa por el itinerario de aprendizaje, permite buscar y se acompaña de un esquema en la página en pantallas anchas. Los enlaces anterior/siguiente siguen el orden publicado del manifiesto. El lector expone solo Markdown empaquetado y permitido; no tiene explorador arbitrario del sistema de archivos ni endpoint de edición.

## Full Output

Full Output representa texto de proveedor retenido para inspección humana tras eliminar secuencias de control ANSI/VT y manipulación del cursor de terminal. La sanitización evita que los controles de presentación reescriban el historial visible; no reinterpreta, ejecuta ni certifica el texto del proveedor. Si Full Cleanup eliminó de forma segura el registro antiguo de un agente salido y conservó sus metadatos, el visor informa de que la salida duradera no está disponible en lugar de mostrar un error o contenido inventado.

## Instalar como aplicación

Los navegadores Chromium compatibles pueden instalar ThreadCells desde la acción de instalación del navegador. El manifiesto utiliza la marca ThreadCells y abre en modo de visualización independiente. En iOS se puede usar **Add to Home Screen**.

Cuando el acceso del operador está protegido por credenciales de navegador, el manifiesto y las solicitudes relacionadas del mismo origen usan el mismo límite de credenciales. El acceso entre orígenes continúa limitado a los orígenes explícitamente confiables; los metadatos PWA no eluden los controles de operador ni de acceso remoto.

El service worker conservador almacena en caché solo recursos estáticos inmutables con huella digital. Nunca almacena en caché navegación HTML, APIs, autorización del operador, agentes, sesiones, flujos de trabajo, resultados, Statistics, terminales, WebSockets ni mutaciones. Si el servidor no está disponible, la aplicación instalada informa del fallo real de red en lugar de presentar estado operativo obsoleto.

Una nueva compilación inmutable reemplaza los recursos antiguos con huella digital mediante el ciclo normal de actualización del service worker del navegador. ThreadCells no mantiene al operador en una shell sin conexión obsoleta.

## Uso adaptable y con teclado

La navegación principal, Docs, Settings, tablas y controles de terminal admiten anchos de teléfono, tableta y escritorio. Las tablas operativas anchas se desplazan horizontalmente en pantallas estrechas en lugar de reducir valores hasta hacerlos ilegibles.

En teléfonos, cada encabezado de sesión de Home usa una fila dedicada al nombre y otra fila separada de metadatos/acciones. Las tarjetas de agente usan siempre la lista canónica de una columna; el selector List/Grid está oculto. Los diseños de tableta y escritorio conservan su elección List/Grid.

Usa la navegación normal Tab/Shift-Tab e indicadores de foco visibles. Los bloques de código de Docs se desplazan horizontalmente y proporcionan un control de copia. El comportamiento de teclado del terminal sigue siendo nativo del proveedor; el desplazamiento táctil no debe inyectar entrada en el terminal.

## Límite de acceso

La UI y Docs ordinarios no ofrecen inicio de sesión general de usuarios. Mantén ThreadCells en loopback. Usa un túnel SSH o un proxy Caddy/Authelia autenticado de [Acceso remoto](REMOTE_ACCESS.md); nunca publiques el puerto 9889 directamente.
