---
slug: overview
source: docs/OVERVIEW.md
source_sha256: sha256:c4082c5da946df3936a8eb1c711b4701ba75b6dfa5000d82bc5f5416d8322f3e
---

# Empieza aquí: ¿qué es ThreadCells?

ThreadCells es un sistema autohospedado para ejecutar varios agentes de programación como un flujo de trabajo coordinado en una máquina Linux. Proporciona a los agentes terminales reales y Git worktrees, mantiene abiertas las misiones entre turnos de modelo y conserva para el operador el control de capacidad, acceso de escritura, cambios protegidos y resultado final.

Si sabes usar Git, SSH y un agente de programación de línea de comandos, ya tienes la base necesaria para empezar. No necesitas entender la arquitectura interna de ThreadCells antes de iniciar trabajo útil.

## ¿Por qué usarlo?

Un solo terminal de agente de programación es fácil de entender. Varios terminales resultan más difíciles: dos agentes pueden editar la misma rama, una compilación puede agotar la memoria, un supervisor puede desaparecer antes de recoger una revisión y que un terminal termine no implica necesariamente que haya terminado la misión solicitada.

ThreadCells hace explícitas esas relaciones y mantiene su propio entorno operativo. Es especialmente útil cuando quieres:

- mantener visibles y reconectables agentes de larga duración;
- dar a trabajadores en paralelo worktrees gestionados e independientes;
- permitir que un supervisor delegue implementación y revisión;
- recibir resultados y mensajes de Inbox sin copiarlos manualmente entre terminales;
- continuar una misma misión lógica entre turnos de proveedor y reinicios normales;
- limitar por separado los turnos de modelo, el trabajo activo y las tareas pesadas del host;
- conservar resultados incluso después de que un terminal salga;
- supervisar la presión del host y limpiar de forma segura los residuos desechables de runtime, registros, caché, compilación y publicación de ThreadCells;
- exigir una decisión del propietario antes de un paso sensible o ambiguo.

ThreadCells está diseñado para un operador de confianza o un pequeño equipo de confianza en un host que controlan. No es un sandbox multiinquilino hostil.

## El ciclo básico

```text
Create a session and choose a project and agent
        ↓
Give the agent or supervisor the job
        ↓
Watch the coordinated workflow and host state
        ↓
ThreadCells continues eligible work across model turns
        ↓
Step in only for an explicit owner decision or final review
```

El agente sigue ejecutándose mediante la CLI nativa de su proveedor. ThreadCells coordina el trabajo alrededor; no sustituye al proveedor. Housekeeping protege el trabajo activo, el estado duradero, las copias de seguridad y las versiones actuales/de recuperación, y recupera solo candidatos cuya propiedad y elegibilidad se puedan demostrar. Esto reduce la supervisión manual de los residuos de ThreadCells, pero no garantiza que el host físico nunca pueda fallar.

## Una primera hora útil

1. Sigue la [configuración rápida](../QUICK_SETUP.md) para compilar y verificar un candidato local.
2. Usa [Instalación](INSTALLATION.md) si quieres conocer el motivo de cada paso o necesitas ayuda con los requisitos previos.
3. Sigue [Tu primer proyecto y agente](FIRST_AGENT.md).
4. Lee [Conceptos básicos](CONCEPTS.md) después de ver funcionar un agente.
5. Antes de usar otra máquina, elige un método seguro en [Acceso remoto](REMOTE_ACCESS.md).

Después, [Proveedores](PROVIDERS.md), [Perfiles](PROFILES.md) y [Flujos de trabajo y resultados duraderos](WORKFLOWS_AND_RESULTS.md) explican el modelo operativo principal. [Operaciones](OPERATIONS.md) cubre las comprobaciones rutinarias para mantener sana una instalación.

## Lo que ThreadCells no hace

Los worktrees de ThreadCells organizan las escrituras; no aíslan en un sandbox a un agente respecto del host. ThreadCells tampoco añade protección general de inicio de sesión a la Web UI. Mantén el servidor solo en loopback y usa reenvío SSH o un proxy inverso autenticado para el acceso remoto.

La versión actual es una vista previa técnica. Lee [Modelo de seguridad](SECURITY_MODEL.md) y [Limitaciones](LIMITATIONS.md) antes de poner repositorios valiosos bajo control de agentes.

## Creador y mantenedor

ThreadCells fue creado y es mantenido por [Subaev Ruslan](https://github.com/IUnknown404I), con contribuciones de la comunidad de ThreadCells. Surgió de la necesidad práctica de operar varios agentes de programación CLI nativos con un control operativo más sólido, resultados duraderos y seguridad de recursos.
