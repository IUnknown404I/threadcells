---
slug: security-model
source: docs/SECURITY_MODEL.md
source_sha256: sha256:6305e6199bae4706af6ed41e99eb0465ed0877bff4e83a7b1df57019f1a3383c
---
# Modelo de seguridad

ThreadCells está pensado para un host Linux de confianza operado por una persona o un pequeño equipo de confianza. Coordina potentes agentes de programación nativos; no es un sandbox para usuarios, prompts, repositorios o plugins de proveedores hostiles.

## Límites prácticos de confianza

### Host y usuario de runtime

Todo aquello que la cuenta del sistema operativo de ThreadCells pueda leer o ejecutar puede estar al alcance de un agente nativo. Use una cuenta dedicada, permisos mínimos del sistema de archivos, instalaciones de proveedores revisadas y repositorios adecuados para la automatización.

Los worktrees administrados separan a los escritores, pero no los contienen. No entregue a la cuenta de runtime credenciales ni acceso al host que un agente no necesite.

### Acceso web

La UI ordinaria y los Docs empaquetados no implementan un inicio de sesión general de usuarios. Vincule a `127.0.0.1`. Use un túnel SSH para accesos ocasionales, o Caddy más Authelia para una URL HTTPS autenticada. Nunca exponga públicamente el puerto sin procesar de ThreadCells.

Una PWA instalada conserva el mismo límite de confianza de red. Su service worker no puede proporcionar estado operativo sin conexión y no almacena en caché API, terminales, autorización, workflows, resultados ni Statistics.

### Autorización del operador

Las mutaciones sensibles del plano de control usan un verificador de operador independiente, provisionado por un principal de sistema operativo distinto. La longitud mínima exacta del secreto es de cinco caracteres; se recomiendan valores más largos generados aleatoriamente.

ThreadCells almacena un verificador scrypt con sal, resúmenes de sesión/grant de corta duración, ámbito, emisor, vencimiento, consumo y registros de auditoría, no el texto sin cifrar. El verificador y cada directorio padre no deben poder ser reemplazados por la cuenta de servicio. La sesión del navegador de cinco minutos usa una cookie `HttpOnly`, `SameSite=Strict`.

Este límite protege las mutaciones configuradas. No convierte toda la Web UI en una aplicación autenticada multiusuario.

### Lanzamiento excepcional del propietario

Los lanzamientos owner-executor/XHigh requieren una capability de un solo uso vinculada a la revisión inmutable exacta del perfil, la revisión de configuración del proveedor, el proyecto/worktree, la solicitud de sesión, la topología, el emisor y la profundidad de delegación. Se consume atómicamente junto con los metadatos del terminal.

Los flujos Web Create Session y Add Agent para el `critical_sol_xhigh_owner` integrado requieren la misma advertencia excepcional, confirmación explícita, desbloqueo del operador y capability acotada de un solo uso. Add Agent vincula el grant a la sesión existente y al worktree canónico resuelto, en vez de aceptar una ruta introducida por el usuario. La ruta local `critical_sol_xhigh_owner --owner-xhigh` requiere confirmación interactiva explícita y lanzamiento solo en loopback. Ninguna de estas rutas concede autoridad a elementos secundarios ni a Web Settings no relacionados. El texto de un prompt no puede acuñar ni delegar autoridad de propietario.

### Proveedores y artefactos importados

Los adaptadores de proveedores son paquetes ejecutables de confianza y requieren revisión del operador. El JSON de proveedor/perfil es entrada declarativa no confiable: se rechazan rutas ejecutables, comandos, indicadores de shell, entornos, secretos sin procesar, comandos MCP arbitrarios y autoridad comodín no concedida.

La autenticación del proveedor permanece en el mecanismo compatible del propio proveedor. Las exportaciones del registro omiten los valores secretos y los grants de un solo uso.

Las credenciales de control por terminal están limitadas al proceso del terminal/proveedor. ThreadCells inicia el servidor tmux de larga duración mediante un bootstrap sin credenciales, por lo que su línea de comandos de proceso persistente no conserva una credencial de terminal. Estas credenciales siguen siendo sensibles para procesos que se ejecutan con la misma cuenta de runtime de confianza.

### Notificaciones de Telegram

La entrega a Telegram es opcional, está deshabilitada de forma predeterminada, es global a la instalación e independiente de la configuración del proyecto. Su token de bot es una configuración Web de solo escritura almacenada fuera de SQLite en la raíz de estado privada de ThreadCells como un archivo regular `0600` propiedad del runtime. Las API de lectura exponen únicamente el estado seguro de configuración. La autorización del operador protege las actualizaciones y las acciones explícitas de conexión/prueba.

Los mensajes de ciclo de vida usan resúmenes seguros fijos y omiten prompts, salida del terminal, cuerpos de excepciones, rutas y credenciales. La entrega externa es de fallo abierto para la ejecución de workflows y se desduplica de forma duradera; habilitar las notificaciones no reproduce eventos observados mientras estaban deshabilitadas.

## Sensibilidad de los datos

Trate como sensibles la base de datos SQLite, los registros de terminal, prompts, resultados, adjuntos, worktrees administrados, copias de seguridad, el verificador del operador y el historial de despliegue nativo de proveedores. Pueden contener código propietario o contenido suministrado por el usuario aunque ThreadCells evite registrar credenciales.

No incluya secretos de operador/proveedor/Telegram en texto sin cifrar en repositorios, volcados de entorno, paquetes de soporte, telemetría, almacenamiento del navegador, respuestas de API ni capturas de pantalla.

## Operaciones destructivas

El housekeeping se planifica primero y falla de forma cerrada. Los recursos desconocidos, ilegibles, abiertos, activos, referenciados, con identidad modificada o con metadatos incompletos permanecen protegidos. Las copias de seguridad nunca se eliminan automáticamente.

El despliegue conserva un runtime de reversión y una copia de seguridad de la base de datos. La publicación, la exposición pública a la red y los cambios destructivos del historial siguen siendo decisiones independientes del propietario.

## Responsabilidades del operador

- Aplique parches al SO, ThreadCells, las CLI de proveedores, el proxy inverso y la capa de autenticación.
- Revise prompts, perfiles, adaptadores y repositorios antes de conceder acceso de escritura.
- Mantenga al usuario de runtime y al entorno de servicio con privilegios mínimos.
- Haga copias de seguridad y pruebe la restauración del estado duradero.
- Inspeccione diffs/resultados antes de una fusión, despliegue o publicación.
- Conserve controles de acceso de loopback o de proxy autenticado.
- Rote las credenciales de proveedores y reemplace el verificador del operador mediante un proceso administrativo seguro.

## Informar de un problema de seguridad

Siga [SECURITY.md](../SECURITY.md). No incluya credenciales activas, estado privado ni detalles públicos de exploits más allá de lo que los mantenedores necesitan para una reproducción segura.
