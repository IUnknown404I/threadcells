---
slug: architecture
source: docs/ARCHITECTURE.md
source_sha256: sha256:0fa43fdddc696e3203367cd85ab6b0ca6ec9d4c03753ebdfea3fc1d336507447
---

# Arquitectura

ThreadCells es un plano de control local en torno a procesos nativos de agentes de programación. Mantiene deliberadamente el terminal del proveedor, el repositorio Git, el estado de coordinación duradero y la UI del navegador como componentes independientes con límites explícitos.

Empiece por [Conceptos básicos](CONCEPTS.md) si los términos siguientes le resultan nuevos.

## Vista del sistema

```text
Browser or installed PWA
        ↓ HTTP / WebSocket on loopback
FastAPI ThreadCells server
  ├── SQLite durable state
  ├── provider/profile registries
  ├── workflow and result service
  ├── capacity and Housekeeping service
  └── tmux/provider adapter control
               ↓
        Native provider CLIs
               ↓
      Git repositories/worktrees
```

## Servidor y UI web

El servidor FastAPI expone la aplicación/API y sirve una compilación Web de producción. La UI de React lee el estado operativo en vivo y se conecta a flujos de terminal mediante WebSockets.

El worker básico de la PWA almacena en caché únicamente activos estáticos con huella digital. HTML, API, autorización, sesiones, flujos de trabajo, Statistics, terminales, mutaciones y WebSockets siguen dependiendo de la red para que la UI no pueda inventar un estado de plano de control sin conexión.

El paquete Docs se genera durante la compilación a partir de `DOCS_MANIFEST.json`. Solo Markdown público incluido en la lista permitida entra en el runtime.

## Estado duradero

SQLite contiene sesiones, terminales, proyectos, revisiones de perfil/proveedor, arrendamientos de recursos, flujos de trabajo, resultados, registros de uso, eventos de auditoría y recibos de programación. Las operaciones que deben ejecutarse exactamente una vez o ser seguras ante repetición usan identidades estables y transacciones de base de datos en vez de depender de una salida transitoria de terminal.

Los procesos de proveedores y las sesiones tmux son hechos externos del runtime. El inicio/la recuperación los reconcilia con la base de datos; no debe asumir que la existencia en un lado prueba que el otro esté actualizado.

## Ejecución de proveedores

Un adaptador traduce un inicio normalizado de ThreadCells en una invocación de CLI nativa revisada. El proveedor sigue mostrando su propia UI de terminal y mantiene su propia autenticación. Los adaptadores informan capacidades y la realidad del preflight en vez de simular comportamientos no compatibles.

La telemetría estructurada de proveedores se normaliza en registros de uso duraderos. Los contadores acumulativos usan puntos de control estables para que el sondeo y el reinicio no dupliquen los totales.

## Contextos de trabajo Git

Los worktrees administrados comparten la base de datos de objetos del repositorio, pero aíslan las rutas de checkout y las ramas. La autoridad de escritura mantiene explícita la propiedad de las mutaciones. Los worktrees son herramientas de concurrencia, no sandboxes del sistema operativo.

## Flujos de trabajo y resultados

El estado del flujo de trabajo sobrevive a los turnos individuales del proveedor. Los resultados delegados se registran, se entregan al menos una vez, el padre los incorpora y se confirman antes de que el hijo pueda retirarse. La finalización explícita —no el mensaje final de un modelo— cierra la misión de nivel superior.

## Admisión y presión

Los supervisores residentes, las ejecuciones de proveedores, los contextos de trabajo y las ejecuciones pesadas tienen límites y arrendamientos independientes. La presión de disco y la protección de Housekeeping son restricciones adicionales del runtime. Las barreras entre procesos garantizan que dos procesos no puedan creer a la vez que adquirieron la última ranura.

## Límite de seguridad

ThreadCells asume un único host y entorno de operador de confianza. El acceso general a la UI se protege externamente mediante loopback/SSH o un proxy inverso autenticado. Las mutaciones sensibles de Settings usan un límite distinto de verificador/sesión de operador, pero no es un sistema de inicio de sesión general.

Los paquetes de proveedores y las CLI nativas son código ejecutable de confianza. La configuración importada es datos declarativos restringidos. Consulte el [Modelo de seguridad](SECURITY_MODEL.md).
