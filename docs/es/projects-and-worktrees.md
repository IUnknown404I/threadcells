---
slug: projects-and-worktrees
source: docs/PROJECTS_AND_WORKTREES.md
source_sha256: sha256:c296e8fec6654451a29dbef47bde79d19fda28f7bbfdf926c857e2cc8508ad3a
---

# Proyectos y worktrees gestionados

Un proyecto ThreadCells es un repositorio Git registrado y la autoridad canónica del código fuente. Da a sesiones, perfiles, estadísticas y flujos de trabajo un lugar estable al que pertenecer, pero no es el directorio escribible normal de un supervisor nuevo. ThreadCells nunca hace seguro un repositorio por el mero hecho de registrarlo, así que comienza con un estado limpio y comprende el límite de escritura que concedes.

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
  ├── Session A supervisor worktree
  ├── Session B supervisor worktree
  ├── developer worktree
  └── reviewer worktree or read-only context
```

ThreadCells registra la relación en lugar de tratar directorios temporales como anónimos. Esto hace más segura la limpieza y atribución de resultados.

Cada nueva Sesión de supervisor asociada a un Proyecto, incluida la primera, recibe un worktree gestionado y una rama únicos sobre una revisión base registrada exactamente. Una segunda Sesión del mismo Proyecto recibe otro worktree; la capacidad residente sigue siendo global. Una Sesión sigue teniendo un solo supervisor principal y cada contexto escribible/worktree sigue teniendo como máximo un lease de escritura. Para sustituir un supervisor inutilizable en el mismo contexto se usa un recovery takeover explícito que conserva el worktree de ese contexto en lugar de crear uno independiente.

Las Sesiones legacy activas anteriores a este contrato permanecen en su workspace existente. ThreadCells no mueve, restablece, limpia, guarda con stash ni copia su estado sucio durante la actualización; las Sesiones nuevas usan worktrees gestionados.

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
