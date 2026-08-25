---
slug: projects-and-worktrees
source: docs/PROJECTS_AND_WORKTREES.md
source_sha256: sha256:330c4175df07b3a91dc7d9e0c88bbf91d6c3bb7b2bb76fd4340828260562dd02
---

# Proyectos y worktrees gestionados

Un proyecto ThreadCells es un repositorio Git registrado. Da a sesiones, perfiles, estadísticas y flujos de trabajo un lugar estable al que pertenecer. ThreadCells nunca hace seguro un repositorio por el mero hecho de registrarlo, así que comienza con un estado limpio y comprende el límite de escritura que concedes.

## Registrar un proyecto

Usa el selector de proyectos en Spawn Agent para elegir un repositorio existente o añade el repositorio mediante el control de proyectos compatible. Usa una ruta canónica absoluta y confirma que el usuario de runtime de ThreadCells puede leerla.

Antes del primer agente:

```bash
git -C /path/to/project status --short
git -C /path/to/project worktree list
```

Resultado esperado: puedes distinguir los cambios y worktrees preexistentes de cualquier cosa que ThreadCells cree después. El trabajo sin confirmar existente pertenece al operador; los agentes no deben descartarlo.

## Por qué existen los worktrees gestionados

Dos escritores en un checkout pueden sobrescribir los cambios del otro incluso si sus prompts no están relacionados. Un Git worktree gestionado da a cada escritor acotado su propio checkout y rama, a la vez que comparte la base de datos de objetos del repositorio.

```text
Canonical repository
  ├── operator checkout
  ├── supervisor context
  ├── developer worktree
  └── reviewer worktree or read-only context
```

ThreadCells registra la relación en lugar de tratar directorios temporales como anónimos. Esto hace más segura la limpieza y atribución de resultados.

## Autoridad de escritura

Solo el contexto que tiene autoridad de escritura debe modificar un worktree gestionado. Los revisores pueden inspeccionar diffs y ejecutar comprobaciones seguras sin convertirse en un segundo escritor no rastreado.

No edites manualmente un worktree gestionado mientras su agente está activo. Si es necesaria una intervención de emergencia, detén o coordina primero con el escritor y registra qué cambió.

## Recuperar el trabajo

Un resultado duradero debe nombrar los archivos modificados y las comprobaciones, pero Git sigue siendo la fuente de verdad para el código. Revisa el estado, diff y commits del worktree antes de fusionar o hacer cherry-pick mediante el proceso habitual de tu repositorio.

ThreadCells no concede autoridad de publicación. Un resultado de trabajador correcto no autoriza hacer push, etiquetar, desplegar ni reescribir el historial.

## Limpieza

Housekeeping elimina un worktree gestionado solo cuando puede demostrar que el worktree ya no está protegido por un terminal activo, flujo de trabajo, lease de escritura o resultado no incorporado. La propiedad desconocida falla de forma cerrada.

Si el uso de disco es alto, planifica primero Housekeeping. No elimines directamente un directorio de worktree; podrías dejar los metadatos de Git y el estado de ThreadCells incoherentes.

## Errores comunes

- Empezar desde un repositorio con cambios sin registrar los cambios existentes.
- Dar a dos agentes autoridad de escritura sobre el mismo checkout.
- Tratar un worktree como sandbox de seguridad.
- Eliminar un worktree antes de incorporar su resultado y commits.
- Suponer que una rama gestionada se fusiona o publica automáticamente.

Consulta [Flujos de trabajo y resultados duraderos](WORKFLOWS_AND_RESULTS.md) para saber cómo llegan los resultados de un worktree a un supervisor.
