---
source_path: docs/OPERATIONS.md
source_sha256: 730fb5dd4e2411ee28adea1e7df752307512484828c2861f015e24ddfd031573
---

# Operaciones

La operación rutinaria de ThreadCells consiste sobre todo en preservar cuatro clases de verdad: la identidad de la compilación en ejecución, la propiedad del flujo de trabajo, la capacidad disponible y el estado recuperable.

## Comprobaciones diarias

Use Inicio, Agents, Settings → General y Settings → Housekeeping para responder:

- ¿El servidor está en buen estado y se ejecuta la compilación esperada?
- ¿El disco y la capacidad están en GREEN, YELLOW o RED?
- ¿Qué supervisores y workers están realmente activos?
- ¿Hay resultados entregados pero no incorporados?
- ¿Hay un flujo de trabajo esperando una decisión del propietario?
- Si Telegram está habilitado, ¿Settings → Telegram muestra el estado seguro esperado de conexión/prueba?

La vista de capacidad en la línea de comandos es:

```bash
threadcells-resource-status
```

Use el endpoint de salud local para monitorizar el servicio:

```bash
curl -fsS http://127.0.0.1:9889/health
```

## Inicio y detención

Ejecute `threadcells-server` en loopback o use el servicio instalado canónico. Una desconexión del navegador no detiene los agentes respaldados por tmux. Un reinicio de servidor compatible conserva los runtimes de terminal legítimamente activos y después rehidrata el estado duradero de flujos de trabajo abiertos y de entrega de Inbox. Los runtimes cerrados se retiran por identidad exacta de terminal/proceso; los registros históricos de sesiones y resultados no dependen de que un panel tmux siga vivo.

Antes de un reinicio planificado:

1. inspeccione el trabajo de proveedores y pesado activo;
2. evite interrumpir una mutación cuando sea posible;
3. registre las identidades actuales de compilación activa y de rollback;
4. cree una copia de seguridad y compruebe la integridad de la base de datos para una actualización;
5. reinicie solo los servicios ThreadCells necesarios;
6. vuelva a conectarse y verifique flujos de trabajo/resultados antes de reintentar nada.

Use Graceful Exit para el ciclo de vida del proveedor. Matar tmux o eliminar filas de la base de datos manualmente puede separar el estado de terminal de la verdad duradera del flujo de trabajo.

## Higiene de sesiones y flujos de trabajo

Un hijo que ha salido no es desechable de inmediato. Confirme que su resultado duradero se haya entregado, leído, incorporado y confirmado. Después retire sus recursos de runtime conservando el historial.

Un mensaje final del proveedor no cierra una misión abierta. Complete explícitamente un flujo de trabajo de nivel superior solo después de terminar todo el trabajo autorizado por el propietario. Use la puerta del propietario solo ante un límite real de decisión.

## Cambios de capacidad

Settings → Orchestration Capacity aplica cambios sin reiniciar el servidor. Las reducciones drenan; no eliminan sesiones activas. Cambie una restricción cada vez y observe si mejora la cola prevista.

Las mutaciones de capacidad requieren una sesión de operador desbloqueada y se auditan. Consulte [Modelo de capacidad y recursos](RESOURCE_MODEL.md).

## Registros y evidencias

Conserve suficientes registros e historial de resultados para diagnosticar una ejecución fallida, pero no trate los registros como la única verdad duradera. La base de datos, el resultado del flujo de trabajo, el commit/diff Git, el manifiesto del candidato y la evidencia de pruebas responden cada uno a preguntas diferentes.

Evite registrar prompts o valores que contengan credenciales. Los errores públicos/de API de ThreadCells deben ser seguros para mostrar.

## Housekeeping

Housekeeping siempre empieza por un plan. Inspeccione la lista de candidatos del dry-run y la identidad del plan, y después ejecute explícitamente el plan exacto. El ejecutor reconstruye la protección actual y vuelve a validar cada candidato antes de mutar. Puede retirar runtimes de terminal cerrados probados y worktrees reconocidos pendientes de limpieza sin borrar el historial duradero.

Las copias de seguridad son solo de inventario y nunca se eliminan automáticamente. Los recursos desconocidos o activos permanecen protegidos. Consulte [Housekeeping](HOUSEKEEPING.md).

## Disciplina de cambios de producción

Para una actualización:

1. compile y verifique un candidato inmutable a partir de un commit exacto;
2. conserve la instalación actual como rollback;
3. cree una copia de seguridad y compruebe la integridad de la base de datos;
4. prepare mediante el mecanismo de despliegue canónico;
5. promocione el candidato preparado exacto;
6. reinicie solo los servicios necesarios;
7. realice pruebas de humo de salud, UI, preflight del proveedor, autorización de operador, flujos de trabajo, terminales y notificaciones globales configuradas de Telegram.

No publique, haga push, etiquete ni cambie la exposición pública como parte incidental de un despliegue local. Consulte [Actualización](UPGRADING.md) y [Despliegue](DEPLOYMENT.md).

## Cuando algo parece incorrecto

Conserve las evidencias antes de limpiar o reintentar. Registre la identidad de la compilación, los ID de sesión/terminal/flujo de trabajo, el mensaje de error seguro, la ventana de registro pertinente, el estado Git y la capacidad actual. Después use la guía [Solución de problemas](TROUBLESHOOTING.md), organizada por síntomas.
