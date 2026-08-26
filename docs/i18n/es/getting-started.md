---
slug: getting-started
source: QUICK_SETUP.md
source_sha256: sha256:321ac8cca8705ac1a90bce08efb278d8296e053fe2377b6f21412fb3b99efc90
---

# Configuración rápida de ThreadCells

Este es el camino compatible más rápido desde un checkout del código fuente hasta un servidor ThreadCells local. Compila un candidato local inmutable, verifica su contenido, lo instala bajo el repositorio actual y escucha solo en loopback.

Para requisitos previos, explicaciones de fallos e instalación como servicio, consulta la [guía completa de instalación](docs/INSTALLATION.md).

## 1. Comprueba el host

ThreadCells se dirige actualmente a Linux Ubuntu/Debian con Python 3, Git, tmux, Node.js/npm para la compilación Web y al menos una CLI de proveedor compatible. Codex es el proveedor principal probado.

Desde la raíz del repositorio:

```bash
python3 --version
git --version
tmux -V
node --version
npm --version
```

## 2. Compila y verifica un candidato

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.0a2-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

Resultado esperado: la verificación se completa correctamente para el manifiesto, archivos, sumas de comprobación y Web UI empaquetada del candidato. Un candidato es un directorio autocontenido con forma de versión; mantenerlo inmutable hace identificable la compilación en ejecución y facilita la reversión.

## 3. Previsualiza y luego instala

```bash
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --dry-run
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --prefix "$PWD/.threadcells"
```

Resultado esperado: la ejecución en seco explica sus destinos sin modificarlos; después, la instalación crea `.threadcells` con un entorno Python y comandos ThreadCells.

## 4. Ejecuta diagnósticos

```bash
"$PWD/.threadcells/venv/bin/threadcells" doctor
```

Resuelve las comprobaciones requeridas que fallen antes de iniciar agentes. Un proveedor opcional puede seguir ausente; aparecerá como **CLI not installed** en la UI.

## 5. Inicia el servidor

```bash
"$PWD/.threadcells/venv/bin/threadcells-server" --host 127.0.0.1 --port 9889
```

Abre `http://127.0.0.1:9889`.

Resultado esperado: se carga Home, Settings → About muestra la identidad de la compilación en ejecución y esta documentación está disponible en Docs.

Mantén el host y el puerto exactamente solo en loopback para esta primera ejecución. Para otra computadora, no cambies el listener a `0.0.0.0`; usa [Acceso remoto](docs/REMOTE_ACCESS.md).

El modelo operativo es deliberadamente breve: crea una sesión, elige un agente o supervisor, dale el trabajo, observa el flujo de trabajo e intervén solo ante una decisión explícita del propietario o revisión final. La finalización del proveedor por sí sola no cierra un flujo de trabajo abierto.

## 6. Inicia trabajo útil

Sigue [Tu primer proyecto y agente](docs/FIRST_AGENT.md). El [ejemplo inicial seguro](examples/threadcells-starter/README.md) incluido también es un ejercicio acotado de supervisor/desarrollador/revisor que no publica ni modifica servicios.

## Detener y reanudar

Detén el servidor en primer plano con `Ctrl-C`. Los terminales de agentes se respaldan en tmux y pueden sobrevivir a la desconexión del navegador, pero no supongas que un servidor interrumpido completó sus flujos de trabajo. Reinicia el mismo `threadcells-server` instalado, abre Agents e inspecciona su estado actual y resultados duraderos.

## Lecturas siguientes

- [Conceptos básicos](docs/CONCEPTS.md)
- [Proveedores](docs/PROVIDERS.md) y [Perfiles](docs/PROFILES.md)
- [Capacidad y modelo de recursos](docs/RESOURCE_MODEL.md)
- [Housekeeping](docs/HOUSEKEEPING.md)
- [Notificaciones de Telegram](docs/TELEGRAM_NOTIFICATIONS.md)
- [Copia de seguridad y restauración](docs/BACKUP_AND_RESTORE.md)
- [Modelo de seguridad](docs/SECURITY_MODEL.md)
