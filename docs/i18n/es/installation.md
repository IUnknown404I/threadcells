---
slug: installation
source: docs/INSTALLATION.md
source_sha256: sha256:9a39c132bdc33d59d9269595d4d260c9977ce1d9d265de1fd66014c2e3493db9
---

# Instalación

Esta guía explica la ruta de instalación local compatible y por qué ThreadCells se instala desde un candidato verificado. Si solo quieres los comandos, usa la [configuración rápida](../QUICK_SETUP.md).

## Referencia compatible

La vista previa técnica actual admite un único host Linux Ubuntu/Debian. ThreadCells espera una cuenta de operador de confianza y un checkout Git local. Otras distribuciones Linux pueden funcionar, pero no son la referencia compatible; macOS y Windows pueden acceder remotamente a la Web UI, pero no son hosts ThreadCells compatibles.

## Requisitos previos

Instala o verifica:

- Python 3 y compatibilidad con `venv`;
- Git;
- tmux;
- Node.js y npm para compilar la Web UI empaquetada;
- utilidades POSIX comunes utilizadas por los scripts de versión y servicio;
- una CLI de proveedor compatible, instalada y autenticada para la cuenta que ejecutará ThreadCells.

Comprueba los comandos importantes:

```bash
python3 --version
git --version
tmux -V
node --version
npm --version
```

ThreadCells puede registrar adaptadores cuyas CLIs no estén presentes. No es un fallo de instalación; solo los proveedores que pretendes iniciar deben estar listos. Consulta [Proveedores](PROVIDERS.md).

## Dónde vive el estado

De forma predeterminada, el estado operativo vive en:

```text
~/.aws/cli-agent-orchestrator/
```

El nombre histórico del directorio se conserva por compatibilidad. Puede contener la base de datos SQLite, registros, worktrees gestionados, contexto de agente, adjuntos, artefactos de proveedor y otro estado de runtime. Establece `CAO_HOME_DIR` antes del primer inicio para elegir otra ubicación absoluta.

La aplicación instalada y su estado de runtime son diferentes:

- el **candidato/instalación** contiene código versionado y recursos Web estáticos;
- la **raíz de estado** contiene la base de datos, datos mutables del operador y archivos secretos opcionales y restrictivos propiedad de ThreadCells, como el token de bot de Telegram;
- las CLIs de proveedores pueden conservar en otros lugares sus propias credenciales e historial de despliegue.

Haz copia de seguridad del estado mutable antes de reemplazar una instalación. Nunca confirmes en Git el estado de runtime ni las credenciales de proveedores.

## ¿Por qué un candidato local?

Un candidato es un directorio con forma de versión, compilado desde una revisión exacta del código fuente. Su manifiesto y sumas de comprobación te permiten verificar qué se ejecutará antes de modificar una instalación. La preparación y promoción pueden conservar el candidato anterior para revertir.

Esta disciplina es más deliberada que ejecutar directamente desde un checkout que cambia, pero evita que la Web UI, el código Python, la documentación y la identidad de compilación provengan silenciosamente de revisiones distintas.

## Compila el candidato

Desde la raíz del repositorio:

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.2.0a1-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

Resultado esperado: el verificador acepta el manifiesto, las sumas de comprobación, la documentación empaquetada y los archivos de aplicación. No instales un candidato cuya verificación falle.

## Previsualiza e instala

Elige un prefijo absoluto que la cuenta de runtime pueda ejecutar. El prefijo local al repositorio de abajo resulta cómodo para evaluación:

```bash
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --dry-run
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --prefix "$PWD/.threadcells"
```

La ejecución en seco va intencionadamente primero. Revisa su origen y destino y, después, ejecuta la instalación real.

## Verifica la CLI instalada

```bash
"$PWD/.threadcells/venv/bin/threadcells" info
"$PWD/.threadcells/venv/bin/threadcells" doctor
"$PWD/.threadcells/venv/bin/threadcells" providers list
```

`doctor` es de solo lectura. Resuelve las utilidades del sistema requeridas que falten. La salida del proveedor debe distinguir un adaptador conocido de una CLI instalada y utilizable.

## Inicia localmente

```bash
"$PWD/.threadcells/venv/bin/threadcells-server" --host 127.0.0.1 --port 9889
```

En otra shell:

```bash
curl -fsS http://127.0.0.1:9889/health
```

Abre `http://127.0.0.1:9889`. Comprueba Settings → About y confirma que su versión y revisión coinciden con el candidato que verificaste.

Para una instalación persistente, usa el mecanismo canónico de servicio/despliegue del repositorio descrito en [Despliegue](DEPLOYMENT.md). No improvises una dirección pública de escucha.

## Fallos iniciales

- **`python3 -m venv` falla:** instala el paquete venv de Python de la distribución.
- **Falta `tmux`:** instálalo antes de iniciar agentes; la persistencia de terminales depende de él.
- **No se pueden compilar los recursos Web:** usa la referencia compatible de Node/npm, instala las dependencias bloqueadas y vuelve a compilar el candidato.
- **El proveedor indica CLI not installed:** instala el comando canónico de ese proveedor para el usuario de runtime o elige un proveedor ya preparado.
- **El proveedor está instalado pero no autenticado:** completa el flujo de inicio de sesión del proveedor como usuario de runtime y repite la comprobación previa.
- **El puerto 9889 está ocupado:** detén el proceso local en conflicto o elige otro puerto loopback y úsalo de forma coherente.
- **El navegador de otra máquina no puede conectarse:** esto es normal para un listener loopback. Usa [Acceso remoto](REMOTE_ACCESS.md).

## Límites de eliminación

Eliminar un prefijo de instalación no elimina de forma segura el estado operativo, credenciales de proveedor, repositorios Git, worktrees, copias de seguridad ni definiciones de servicios. Detén ThreadCells, crea una copia de seguridad verificada e identifica cada una de esas categorías por separado. Usa Housekeeping para los artefactos de runtime aptos; no elimines recursivamente la raíz de estado como atajo para desinstalar.

Después, sigue [Tu primer proyecto y agente](FIRST_AGENT.md).
