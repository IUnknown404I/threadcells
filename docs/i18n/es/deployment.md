---
slug: deployment
source: docs/DEPLOYMENT.md
source_sha256: sha256:0a1e81cb71e94e5fe3a400baf7d83caaca2b9abf0a13cc7c7fcd19e62761e835
---

# Despliegue local

El despliegue de ThreadCells promociona un candidato inmutable verificado al runtime local. No implica publicación, un push/tag Git, lanzamiento de paquete ni exposición a una red pública.

## Disciplina de candidatos

Compile a partir de un commit de código fuente limpio y exacto; después verifique el candidato antes de prepararlo:

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
python3 scripts/verify_local_candidate.py \
  --candidate "$PWD/threadcells-candidate/threadcells-0.2.0a1-local"
```

El candidato debe contener código Python, activos Web empaquetados, el paquete Docs incluido en la lista permitida, identidad de compilación, checksums y metadatos de release de la misma revisión.

La preparación del host usa un grupo dedicado de mantenimiento de releases, de modo que el plano de control en ejecución pueda leer pero no reemplazar un candidato inmutable, mientras que los servicios de Housekeeping puedan eliminar un release explícitamente desprotegido. Cree ese grupo de sistema una vez antes de la primera preparación del host:

```bash
sudo groupadd --system threadcells-release-admin
```

Las unidades instaladas de plano de control y Housekeeping deshabilitan las escrituras de bytecode Python. Esto evita que las importaciones rutinarias cambien la propiedad o el contenido dentro de un release inmutable, incluso mientras está activo el grupo de mantenimiento de releases de alcance restringido.

El comando de preparación falla de forma cerrada si este grupo no está disponible. Conserva los candidatos de release, el puntero activo atómico, el bloqueo de preparación y los metadatos de protección de release bajo un ancla `/var/lib/threadcells` propiedad de root, fuera del estado propiedad del runtime. Los servicios de producción se ejecutan mediante `/var/lib/threadcells/active`, no a través de un enlace de comando escribible por el runtime. Las rutas de candidatos deben ser hijos directos de `/var/lib/threadcells/releases`; se rechazan los enlaces simbólicos y objetivos alternativos de bloqueo/metadatos.

## Secuencia de promoción segura

1. Registre el runtime activo actual y su estado de salud.
2. Consérvelo como destino de rollback verificado.
3. Cree y compruebe la integridad de una copia de seguridad de la base de datos.
4. Prepare el candidato exacto verificado mediante el mecanismo de despliegue canónico del repositorio.
5. Verifique de nuevo el candidato preparado.
6. Promocione atómicamente la identidad preparada.
7. Reinicie solo los servicios ThreadCells necesarios.
8. Realice la aceptación de producción en loopback o mediante la ruta de acceso protegida existente.

No sobrescriba el directorio activo in situ. Un puntero/enlace simbólico de release o un mecanismo canónico equivalente debe identificar sin ambigüedad los candidatos activos, de rollback y preparados.

Después de que la preparación haya registrado el candidato exacto, promuévalo mediante la operación canónica bloqueada:

```bash
sudo python3 deployment/promote-ops-p1.py \
  --system-root / \
  --candidate-root /var/lib/threadcells/releases/RELEASE_ID \
  --expected-commit EXACT_PUBLIC_SHA
```

Use `--rollback-root` cuando ya exista un release de rollback canónico verificado. La operación es idempotente: un reintento completa una transición interrumpida de puntero/metadatos sin inventar una nueva identidad de release.

## Aceptación

Compruebe como mínimo:

- la salud y la identidad de compilación en Settings → About;
- Home, Agents, Flows, Statistics, Settings, Docs y Spawn Agent;
- el inventario de proveedores y un preflight seguro;
- el comportamiento de mutación de operador configurado/bloqueado/desbloqueado/protegido;
- el estado de configuración segura global de Telegram y, solo cuando las credenciales nativas ya estén configuradas, el comportamiento explícito de conexión/prueba;
- conexión y reconexión de terminal;
- continuación de flujo de trabajo/resultados;
- integridad de la base de datos y ausencia de duplicación en la repetición de uso;
- registro de manifiesto/iconos/service worker de PWA sin caché dinámica.

## Rollback

El rollback cambia al candidato anterior conservado y reinicia solo los servicios necesarios. Restaure la base de datos solo cuando la nueva versión haya realizado una migración incompatible o dañina; una restauración innecesaria de base de datos puede descartar trabajo válido terminado después de la promoción.

Después del rollback, verifique la identidad de compilación, salud, compatibilidad de esquema, flujos de trabajo activos y terminales. Conserve el candidato fallido y los registros hasta entender la causa raíz.

## Límites

La autoridad de despliegue local no concede permiso para publicar paquetes, hacer push a un remoto, crear una etiqueta/release ni exponer un puerto de servicio sin procesar. Esas siguen siendo decisiones independientes del propietario.
