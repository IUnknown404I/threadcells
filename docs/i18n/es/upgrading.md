---
slug: upgrading
source: docs/UPGRADING.md
source_sha256: sha256:583ec58e621329fd7dc9914a0c29c18a7f12bee09808f2f18908fe4d972536cf
---

# Actualización de ThreadCells

Una actualización es una promoción controlada de candidato con un rollback verificado, no una sobrescritura in situ de los archivos que casualmente se estén ejecutando.

## Antes de la actualización

- Lea las notas de release y [Limitaciones](LIMITATIONS.md).
- Confirme la salud actual y las identidades de compilación activa/de rollback.
- Deje que las operaciones críticas de proveedor/pesadas alcancen un límite seguro.
- Inspeccione los flujos de trabajo abiertos y los resultados entregados.
- Cree una copia de seguridad coherente y ejecute comprobaciones de integridad de la base de datos.
- Conserve el candidato actual como rollback.

## Compilar y verificar

Desde el commit de código fuente previsto:

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.2.0a1-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

No promocione si la identidad del candidato difiere del commit revisado o si fallan las comprobaciones de docs/Web/compilación.

## Preparar y promocionar

Use las herramientas canónicas de despliegue local para preparar el candidato sin cambiar el puntero activo. Verifique los archivos preparados, después promocione de forma atómica y reinicie solo los servicios ThreadCells que consumen el release.

Resultado esperado: Settings → About, el pie de página de Docs y los metadatos de release identifican la misma revisión de candidato.

## Comprobaciones posteriores a la actualización

1. `curl -fsS http://127.0.0.1:9889/health`
2. Abra Home e inspeccione el estado de capacidad/disco.
3. Abra Agents/Flows existentes y confirme que se mantienen las relaciones duraderas.
4. Compare la disponibilidad de proveedores en Settings y Spawn.
5. Confirme que la autorización de operador está configurada y que las mutaciones protegidas siguen bloqueadas hasta desbloquear.
6. Abra Statistics y confirme que una actualización/reinicio no duplica el uso.
7. Abra rutas de Docs y verifique la identidad de la compilación empaquetada.
8. Compruebe el streaming/reconexión de terminal.
9. Verifique que el manifiesto de PWA y el service worker no almacenan solicitudes dinámicas en caché.
10. Abra Settings → Telegram y confirme su estado seguro de configuración; si las credenciales nativas ya estaban configuradas, ejecute las comprobaciones explícitas de conexión y mensaje de prueba.
11. Para un agente abierto que atraviese la promoción, confirme que cualquier reinicialización de conexión de control se complete una vez y que continúe su mismo flujo de trabajo duradero sin una reactivación del propietario ni un hijo/efecto duplicado.

## Reparaciones históricas

Una actualización puede incluir una reparación de datos limitada. Ejecútela solo cuando la evidencia de origen sea determinista, manténgala idempotente y registre los recuentos antes/después. La telemetría de proveedor ausente debe seguir ausente; nunca invente uso histórico.

## Rollback

Si la aceptación falla de forma material:

1. conserve el candidato fallido y los registros seguros pertinentes;
2. cambie el puntero activo canónico al candidato de rollback verificado;
3. reinicie solo los servicios necesarios;
4. verifique la compilación de rollback y las superficies principales;
5. restaure la base de datos previa a la actualización solo si la compatibilidad de esquema/datos lo requiere.

No use Git reset destructivo ni elimine evidencia de runtime más reciente para simular un rollback.

Consulte [Despliegue local](DEPLOYMENT.md) y [Copia de seguridad y restauración](BACKUP_AND_RESTORE.md).
