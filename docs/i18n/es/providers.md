---
slug: providers
source: docs/PROVIDERS.md
source_sha256: sha256:7f782daac9b50583042705af486afbdcc65d19ed545e0d8addd6e918808d7b0f
---

# Proveedores

Un proveedor es la CLI nativa de agente de programación que realmente ejecuta el turno del modelo. ThreadCells proporciona un adaptador alrededor de esa CLI para que los lanzamientos, el estado de los terminales, la cancelación, el informe de capacidades y la telemetría de uso disponible tengan una forma común.

## Tres datos distintos

Las pantallas de proveedores separan deliberadamente tres datos fáciles de confundir:

| Dato | Significado |
| --- | --- |
| Adaptador integrado | Esta compilación de ThreadCells contiene código de integración revisado para el proveedor. |
| CLI instalada | El ejecutable requerido está en el `PATH` del usuario de runtime. |
| Lista | El preflight considera que la CLI instalada es compatible y está autenticada, o la CLI no puede exponer de forma segura el estado de autenticación. |

Settings → Providers muestra los adaptadores, incluso aquellos cuyo comando externo está ausente. Spawn Agent usa el mismo preflight canónico y deshabilita los proveedores cuya indisponibilidad está comprobada.

Por ejemplo, **Adaptador integrado · CLI no instalada** no es contradictorio. Significa que ThreadCells sabe operar el proveedor, pero el host no cuenta actualmente con el programa de ese proveedor.

## Proveedores integrados

La compilación actual registra estos adaptadores:

| Proveedor | Comando canónico |
| --- | --- |
| Amazon Q Developer | `q` |
| Claude Code | `claude` |
| Codex | `codex` |
| Gemini CLI | `gemini` |
| GitHub Copilot CLI | `copilot` |
| Kimi CLI | `kimi` |
| Kiro CLI | `kiro-cli` |
| OpenCode CLI | `opencode` |

El registro es compatibilidad factual del producto, no una instrucción para instalar todas las CLI. Instala solo los proveedores que vayas a usar, siguiendo las instrucciones oficiales y el flujo de autenticación de cada proveedor.

## Matriz de compatibilidad

Esta matriz describe el contrato del adaptador en esta versión, no promete que todas las versiones de una CLI externa o todas las cuentas estén listas en un host concreto. **Compatible** significa que el adaptador implementa directamente la capacidad, **Condicional** indica que el comportamiento depende de la CLI del proveedor o del modo de sesión, y **No informado** significa que ThreadCells no inventa esos datos.

| Proveedor | Inicio/cancelación | Reanudación y persistencia | Finalización estructurada | Telemetría de uso | Controles de modelo/razonamiento | Comprobación de disponibilidad |
| --- | --- | --- | --- | --- | --- | --- |
| Codex | Compatible | Reanudación condicional; persistencia compatible | Condicional | Campos de tokens nativos del proveedor compatibles | Compatible | Comando, versión y autenticación |
| Claude Code | Compatible | Condicional | Condicional | Campos nativos del proveedor condicionales | Selección de modelo compatible; otros controles dependen del adaptador | Comando, versión y autenticación |
| Amazon Q Developer | Compatible | Condicional | Condicional | No informado | Condicional | Comando y versión; autenticación sin verificar |
| Gemini CLI | Compatible | Condicional | Condicional | No informado | Condicional | Comando y versión; autenticación sin verificar |
| GitHub Copilot CLI | Compatible | Condicional | Condicional | No informado | Condicional | Comando y versión; autenticación sin verificar |
| Kimi CLI | Compatible | Condicional | Condicional | No informado | Condicional | Comando y versión; autenticación sin verificar |
| Kiro CLI | Compatible | Condicional | Condicional | No informado | Condicional | Comando y versión; autenticación sin verificar |
| OpenCode CLI | Compatible | Condicional | Condicional | No informado | Condicional | Comando y versión; autenticación sin verificar |

Codex es el proveedor de referencia y de aceptación de la versión. Los demás adaptadores integrados siguen siendo utilizables cuando su preflight público permite el lanzamiento, pero el comportamiento nativo y la autenticación pueden variar según el proveedor. La vista activa de capacidades en Settings es la autoridad para una compilación instalada.

## Etiquetas de disponibilidad

ThreadCells normaliza el preflight en cinco estados orientados al operador:

- **Lista** (`INSTALLED_AND_READY`): instalada, compatible y autenticada cuando se puede comprobar la autenticación.
- **Se requiere autenticación** (`INSTALLED_NOT_AUTHENTICATED`): el comando existe, pero el proveedor indica que se requiere iniciar sesión.
- **Instalada, pero no saludable** (`INSTALLED_BUT_UNHEALTHY`): instalada, pero incompatible o con error en la comprobación de salud/versión.
- **CLI no instalada** (`NOT_INSTALLED`): el ejecutable canónico no se encuentra para el usuario de runtime de ThreadCells.
- **Preparación sin verificar** (`UNKNOWN`): instalada y no probada como no disponible, pero el proveedor no puede verificar de forma segura la autenticación o la preparación sin interacción.

Un proveedor sin verificar puede seguir siendo lanzable cuando su comando está instalado, es compatible y la única incógnita es el estado de autenticación. Un lanzamiento aún puede fallar con un prompt de inicio de sesión propio del proveedor; inspecciona su terminal y completa la autenticación fuera de ThreadCells.

## Comprueba la perspectiva del usuario de runtime

La disponibilidad del proveedor depende de la cuenta que ejecuta ThreadCells, no de tu shell interactivo. Compruébala primero mediante ThreadCells:

```bash
threadcells providers list
threadcells doctor
```

Después, como usuario de runtime, verifica el binario esperado y su versión. Para Codex:

```bash
command -v codex
codex --version
codex login status
```

Usa el comando de estado propio del proveedor cuando exista. No copies directorios de credenciales personales del proveedor a la cuenta de servicio. Autentica esa cuenta mediante el flujo admitido por el proveedor.

## Settings y Spawn Agent

Settings → Providers es la vista de inventario y diagnóstico. Muestra la identidad del adaptador, la configuración, las capacidades, la presencia del comando, la versión, el estado de autenticación y un mensaje de preflight seguro para uso público.

Spawn Agent es la vista de lanzamiento. Deriva su estado habilitado/deshabilitado del mismo resultado de preflight. Si las dos vistas discrepan después de actualizarse, trátalo como un defecto del producto en lugar de adivinar qué etiqueta es correcta.

## Las capacidades son específicas de cada proveedor

Los adaptadores declaran si se admiten, son condicionales o no se admiten la reanudación, la finalización estructurada, la selección de modelo, el control de razonamiento, la persistencia de sesión y el uso. ThreadCells no simula una función no admitida.

Codex es el adaptador de referencia y proporciona telemetría acumulada exacta para los campos de tokens admitidos. Claude Code admite condicionalmente algunas capacidades de uso y finalización. Otros adaptadores pueden no informar de uso; sus campos de Statistics permanecen no disponibles en vez de estimarse.

## Configuración y secretos

La configuración del proveedor es declarativa. Puede seleccionar un adaptador instalado y ajustes propiedad del adaptador, pero no puede importar una ruta de binario, un comando de shell, argumentos, variables de entorno, contraseñas, tokens ni credenciales sin procesar.

Las `secret_refs` opacas pueden nombrar un secreto que resuelve código de adaptador de confianza. Las respuestas públicas de listado y exportación omiten o censuran sus valores. Los paquetes de adaptadores de proveedor son código ejecutable de confianza y deben ser instalados y revisados por el operador del host.

## Solución de problemas

### El proveedor muestra CLI no instalada

Ejecuta `command -v` como cuenta de servicio y compara su `PATH` con el de tu shell. Instala el comando canónico del proveedor solo si pretendes usarlo; después reinicia o actualiza el preflight.

### Instalada, pero se requiere autenticación

Ejecuta el flujo oficial de inicio de sesión del proveedor como usuario de runtime. El preflight de ThreadCells nunca se autentica en tu nombre ni habilita ajustes de omisión de permisos.

### Preparación sin verificar

El comando existe, pero no tiene una sonda de preparación segura y no interactiva. Comprueba la versión y realiza una prueba pequeña nativa del proveedor. Un lanzamiento de ThreadCells puede ser la primera comprobación definitiva de preparación.

### Instalada, pero no saludable

Lee el motivo seguro del preflight. Las causas habituales son un error en el comando de versión, una versión conocida como incompatible o un ejecutable que sale inesperadamente. Actualiza o repara la CLI externa; no edites el registro de adaptadores para marcarla como lista.

### El lanzamiento falla a pesar de estar Lista

Abre la salida del terminal. Las credenciales pueden haber caducado después del preflight, un modelo seleccionado puede no estar disponible o el estado del servicio del proveedor puede haber cambiado.

Para detalles de integración avanzados, consulta [Creación de adaptadores de proveedor](PROVIDER_ADAPTERS.md). Para saber qué controla un perfil de lanzamiento, consulta [Perfiles](PROFILES.md).
