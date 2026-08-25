---
slug: first-agent
source: docs/FIRST_AGENT.md
source_sha256: sha256:0695738b5c690bf05b93bbd5a0afd0e1ab38857a7f488141af3244ce66dae948
---

# Tu primer proyecto y agente

Este tutorial inicia un agente deliberadamente pequeño y muestra dónde encontrar su terminal y resultado. Completa primero la [configuración rápida](../QUICK_SETUP.md) y deja el servidor ThreadCells en ejecución.

## 1. Prepara un repositorio seguro

Usa un repositorio Git desechable o limpio para la primera ejecución. ThreadCells identifica un proyecto por su repositorio y puede crear worktrees gestionados junto a él.

```bash
mkdir -p /tmp/threadcells-first-project
cd /tmp/threadcells-first-project
git init
printf '# First project\n' > README.md
git add README.md
git commit -m 'Create first project'
```

Resultado esperado: `git status --short` no muestra nada. Empezar limpio hace que los cambios del agente sean fáciles de inspeccionar.

## 2. Abre ThreadCells

Abre `http://127.0.0.1:9889` en la máquina que ejecuta ThreadCells. Si el host es remoto, establece primero el túnel SSH descrito en [Acceso remoto](REMOTE_ACCESS.md).

Abre **Spawn Agent**, selecciona el repositorio como proyecto y elige un proveedor instalado. Un proveedor marcado **CLI not installed** no puede iniciar; consulta [Proveedores](PROVIDERS.md) si tu proveedor esperado no está disponible.

Elige un perfil de trabajador de propósito general para esta primera tarea. Escribe un prompt acotado como:

```text
Add a short Usage section to README.md. Do not change any other file.
Run git diff --check and report the changed file.
```

Inicia el agente.

## 3. Observa el terminal

El nuevo agente aparece en **Agents**. Su terminal es una sesión tmux real, por lo que la salida nativa del proveedor sigue visible y reconectable. ThreadCells registra alrededor de ese terminal la identidad del proyecto, perfil, proveedor y sesión.

Resultado esperado: el estado cambia de starting a running, aparece la salida del proveedor y la capacidad refleja una ejecución activa de proveedor mientras el modelo produce un turno.

Si el agente nunca inicia, comprueba la etiqueta de disponibilidad del proveedor y las tarjetas de capacidad. [Solución de problemas](TROUBLESHOOTING.md) contiene comprobaciones por síntoma.

## 4. Inspecciona el trabajo

Cuando el agente termine, inspecciona su resultado duradero y el diff del repositorio. Que un terminal alcance un mensaje final del proveedor es evidencia, pero no da permiso para fusionar, publicar ni desplegar.

```bash
cd /tmp/threadcells-first-project
git status --short
git diff -- README.md
```

Si el agente trabajó en un worktree gestionado, usa la ruta de worktree que muestra ThreadCells en lugar de la ruta del repositorio original. El worktree mantiene separados los escritores concurrentes hasta que sus commits se reconcilien deliberadamente.

## 5. Prueba la supervisión

Cuando un único trabajador tenga sentido, inicia un perfil de supervisor en otra tarea pequeña. Pídele que asigne una tarea de implementación y una revisión independiente. La relación debería verse así:

```text
Owner
  └── Supervisor
        ├── Developer
        └── Reviewer
              ↓
        Durable results return to the supervisor
```

El supervisor sigue siendo responsable de incorporar esos resultados y completar el flujo de trabajo de nivel superior. Que un trabajador termine no cierra la misión del supervisor.

## Siguientes pasos

- Aprende los nombres usados en la UI: [Conceptos básicos](CONCEPTS.md).
- Comprende los perfiles antes de crear otros personalizados: [Perfiles](PROFILES.md).
- Aprende cómo sobrevive la delegación a la finalización del terminal: [Flujos de trabajo y resultados duraderos](WORKFLOWS_AND_RESULTS.md).
- Dimensiona la máquina de forma conservadora: [Capacidad y modelo de recursos](RESOURCE_MODEL.md).
