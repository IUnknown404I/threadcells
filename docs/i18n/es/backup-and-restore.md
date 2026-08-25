---
source_path: docs/BACKUP_AND_RESTORE.md
source_sha256: 3e62f0b30f78fa32bfab783c5fa15e89b9646e2c6de211b8c8ddec3b05b53da1
---

# Copia de seguridad y restauración

Una copia de seguridad útil de ThreadCells conserva el estado de coordinación duradero y la configuración necesaria para interpretarlo. El código instalado y las cachés de compilación normalmente se pueden reconstruir; la base de datos, la configuración del operador y las evidencias nativas del proveedor podrían no poderse reconstruir.

## Qué importa

Haga copia de seguridad, según corresponda, de:

- la base de datos SQLite de ThreadCells y sus archivos SQLite asociados;
- la configuración y el entorno de servicio, excluyendo secretos en texto plano de archivos ad hoc;
- el archivo de verificador del operador como artefacto adyacente a secretos protegido por separado;
- el archivo de token de bot de Telegram, si está configurado, como credencial cifrada por separado con propiedad y modo preservados;
- el contexto del agente, adjuntos y registros requeridos por su política de retención;
- los metadatos de worktrees administrados y releases necesarios para interpretar el trabajo activo;
- los manifiestos/identidades exactos de los candidatos activos y de rollback;
- el estado de proveedores externos solo de acuerdo con la propia política de backup compatible de cada proveedor.

Los repositorios Git ya deben tener su propia estrategia de backup/remoto. Una copia de seguridad de la base de datos de ThreadCells no sustituye la conservación de commits.

## Qué se puede reconstruir

Las dependencias Web descargadas, las revisiones de navegador, las cachés de paquetes, los directorios temporales de compilación y los contenidos de candidatos verificados normalmente se pueden recrear desde el código fuente y los lockfiles. No amplíe cada copia de seguridad con cachés solo porque existan bajo rutas de runtime.

## Secuencia de copia de seguridad coherente

1. Registre la identidad activa de código fuente/candidato y el estado actual del servicio.
2. Evite iniciar nuevas sesiones o mutaciones durante la ventana de snapshot.
3. Use el mecanismo canónico de backup de la base de datos en vez de copiar a ciegas un archivo SQLite activo.
4. Ejecute la verificación de integridad de SQLite en la copia de seguridad.
5. Copie los artefactos requeridos de configuración, verificador y token de Telegram configurado con permisos preservados y sin imprimir su contenido.
6. Registre checksums y almacene el archivo fuera de la raíz de estado activa.
7. Pruebe que el principal de recuperación previsto puede listar y leer la copia de seguridad.

Si las herramientas de despliegue proporcionan un comando de backup, úselo: comprende la ruta real de la base de datos y la coordinación del servicio. Nunca coloque secretos de proveedor u operador en texto plano en el historial del shell para crear un archivo.

## Verificación

Como mínimo, verifique la base de datos SQLite copiada:

```bash
sqlite3 /path/to/backup.db 'PRAGMA integrity_check;'
```

Resultado esperado: `ok`. Registre también un checksum y confirme que el archivo contiene la configuración, el verificador y la identidad de compilación esperados sin exponer su contenido en los registros.

Una copia de seguridad no probada es solo una hipótesis. Ensaye periódicamente la restauración en una ruta aislada y un puerto solo local.

## Orden de restauración

1. Detenga o aísle el servicio ThreadCells de destino.
2. Conserve el estado fallido actual para rollback forense.
3. Instale o seleccione el candidato compatible exacto.
4. Restaure la base de datos y el estado mutable con la propiedad esperada de la cuenta de runtime.
5. Restaure la configuración del servicio.
6. Restaure el verificador del operador con un propietario de confianza distinto, modo legible por el servicio y una cadena de directorios padre confiable; restaure un token de Telegram aplicable en `$CAO_HOME_DIR/secrets/telegram-bot-token` como archivo regular propiedad del runtime con modo `0600`.
7. Ejecute comprobaciones de integridad antes de iniciar.
8. Inicie en loopback y verifique la identidad de salud/compilación.
9. Inspeccione los flujos de trabajo activos, resultados, terminales, proyectos, preflight de proveedores y Statistics antes de reintentar trabajo.

No restaure solo la base de datos dejando código no coincidente o un entorno de servicio obsoleto. No suponga que los procesos tmux/de proveedor sobrevivieron de forma coherente; reconcilie cada proceso vivo con el estado duradero de sesión.

## Validación de recuperación

Después de restaurar, verifique:

- Settings → About coincide con el candidato previsto;
- `/health` responde correctamente;
- los proyectos y el historial de sesiones están presentes;
- los resultados entregados siguen siendo atribuibles;
- la disponibilidad de proveedores refleja la instalación real del usuario de runtime restaurado;
- la autorización de operador informa que está configurada y se desbloquea con el secreto existente;
- Telegram informa su estado esperado de configuración segura y, si se restauró, supera las comprobaciones explícitas de conexión y mensaje de prueba antes de habilitarse;
- los totales de Statistics se reproducen sin duplicación;
- los releases activos/de rollback siguen identificados correctamente.

Las copias de seguridad están protegidas de Housekeeping automático. Aplique una política de retención separada y revisada al almacenamiento de backups.
