---
slug: comparison
source: docs/COMPARISON.md
source_sha256: sha256:43b9f58d4b33db88b6d6271456b89ce9008759f75a7bcf1dbb7ce02657a1ccee
---
# Dónde encaja ThreadCells

ThreadCells está dirigido a desarrolladores que ya valoran las CLI de agentes de programación nativos, pero necesitan una forma más clara de operar varias de ellas en una sola máquina.

## En comparación con ventanas de terminal independientes

Los shells tmux independientes son sencillos, pero no registran automáticamente la identidad de perfil/proveedor, la propiedad de escritores administrados, la admisión de capacidad, la ascendencia de workflows, los resultados duraderos de elementos secundarios ni las compuertas de operador. ThreadCells conserva los terminales nativos mientras añade esos registros operativos.

## En comparación con una plataforma de agentes alojada

ThreadCells es autoalojado y prioriza loopback. Los repositorios, terminales y la base de datos de coordinación permanecen en el host del operador. A cambio, el operador se ocupa de la instalación, autenticación de proveedores, copias de seguridad, aplicación de parches, dimensionamiento de recursos y protección del acceso remoto.

## En comparación con contenedores o sandboxes de seguridad

ThreadCells no es uno de ellos. Los worktrees administrados y las políticas de autoridad reducen los errores de coordinación, pero no aíslan los procesos nativos de proveedores de la cuenta del sistema operativo.

## En comparación con fábricas de software autónomas

ThreadCells prioriza la delegación acotada, terminales inspeccionables, resultados explícitos, decisiones del propietario y finalización respaldada por evidencia. No promete que los agentes puedan entregar software arbitrario sin revisión.

ThreadCells es un proyecto descendiente independiente de AWS Labs CLI Agent Orchestrator y conserva internos `cao` compatibles cuando es necesario. No es un reemplazo directo de productos de agentes no relacionados como OpenHands o Hermes. Elíjalo para operaciones locales de CLI nativas y control duradero de supervisor/trabajador, no para multitenencia alojada ni abstracción amplia de plataforma.
