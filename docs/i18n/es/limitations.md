---
slug: limitations
source: docs/LIMITATIONS.md
source_sha256: sha256:b2b9aa6344d8b7d6c1b1391b1022817b51ce189237616721475510e23dd29cd5
---
# Limitaciones actuales

ThreadCells es una vista previa técnica centrada en operaciones locales fiables para agentes de programación en un host Linux. Estos límites son hechos intencionados del producto, no promesas sobre funciones empresariales sin implementar.

## Plataforma y escala

- La base de host compatible es Ubuntu/Debian Linux.
- Un plano de control local y una base de datos SQLite coordinan una flota modesta en un solo host.
- Los límites de capacidad reducen la contención, pero no crean contenedores estrictos de CPU/memoria ni garantizan rendimiento.
- Las instalaciones muy grandes de varios hosts, de alta disponibilidad o con escalado horizontal quedan fuera del contrato actual.

## Confianza y aislamiento

- Los agentes nativos se ejecutan con el acceso al sistema operativo del usuario de runtime.
- Los worktrees aíslan checkouts de Git, no la seguridad de la red ni del sistema de archivos.
- Los adaptadores de proveedores son paquetes ejecutables de confianza.
- El sistema no está diseñado para multitenencia hostil ni para registro público no confiable.

## Acceso web

- La UI ordinaria no tiene un inicio de sesión general de usuarios integrado.
- El servidor debe permanecer solo en loopback a menos que esté protegido por un proxy HTTPS autenticado externo.
- La autorización del operador protege los ajustes sensibles; no reemplaza el control de acceso externo.
- La PWA instalable depende de la red y no proporciona control de agentes sin conexión.

## Proveedores y telemetría

- La disponibilidad de adaptadores integrados varía según la instalación de la CLI, la compatibilidad y la autenticación del proveedor.
- Algunos proveedores no pueden informar del estado de autenticación de forma no interactiva.
- Los campos de uso existen solo cuando el proveedor ofrece telemetría veraz.
- Statistics es telemetría operativa, no un estado de cuenta de facturación; los valores históricos desconocidos siguen siendo desconocidos.

## Recuperación y automatización

- La recuperación reconcilia el estado duradero con procesos externos tmux/de proveedor, pero no puede volver reversible un comando externo no idempotente.
- Las copias de seguridad y la restauración requieren disciplina del operador y deben ensayarse.
- El housekeeping deja intencionadamente en su lugar los artefactos ambiguos.
- La automatización de publicación y lanzamiento remoto queda intencionadamente fuera del despliegue local ordinario.

Evalúe primero ThreadCells en repositorios no críticos, mantenga copias de seguridad verificadas e inspeccione la salida de los agentes antes de realizar acciones de consecuencias importantes.
