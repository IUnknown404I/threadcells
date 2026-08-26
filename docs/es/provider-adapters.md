---
slug: provider-adapters
source: docs/PROVIDER_ADAPTERS.md
source_sha256: sha256:1b3bda3574765fd4b540f7460e14a1677a3d3dd58be8bf9d07f5fba0c53df1d9
---

# Creación de adaptadores de proveedor

Esta es una guía avanzada para mantenedores que añaden una integración de proveedor de confianza. Los operadores que elijan entre proveedores integrados deberían empezar por [Proveedores](PROVIDERS.md).

La API V1 de adaptadores de proveedor de ThreadCells es un límite de extensión de código de confianza distinto de los plugins de observador. Instala los adaptadores como paquetes Python revisados que registran objetos `ProviderAdapterDefinition` bajo el grupo de puntos de entrada `threadcells.provider_adapters.v1`. Reinicia el candidato/runtime local después de instalar para que se redescubran los puntos de entrada.

## Contrato

Una definición de adaptador proporciona:

- un `AdapterManifest` con `adapter_id` estable, API de plugin `1.0`, versión de implementación, descripción, capacidades y esquema de configuración JSON;
- un modelo Pydantic `AdapterSettings` para ajustes declarativos;
- una fábrica que acepta `ProviderLaunchContext` y ajustes validados;
- una función de preflight que devuelve estado normalizado, instalación, autenticación, versión, compatibilidad, modelos, código de motivo y un mensaje sin secretos.

El proveedor devuelto implementa inicio/reanudación/cancelación normalizados, estado/resultado de terminal, uso y semántica de salud mediante el ciclo de vida existente de `BaseProvider`. Declara honestamente las capacidades no admitidas y condicionales. Nunca sintetices uso que una CLI no haya informado.

## Confianza y configuración

Los paquetes de adaptadores son ejecutables y, por tanto, solo los instala el operador de confianza del host. El JSON del registro no puede elegir binarios ni inyectar comandos. ThreadCells rechaza recursivamente claves de ejecutable, comando, shell, argumento, bandera, entorno, credencial, contraseña, token y secreto. Los secretos sin procesar nunca pertenecen a `settings`; usa `secret_refs` opacas semánticas y resuélvelas solo dentro del código de adaptador de confianza según la política de secretos de la instalación.

Mantén los errores normalizados con códigos de motivo estables y mensajes seguros para uso público. El preflight no debe mutar los ajustes del proveedor ni autenticarse en nombre del operador.

## Ejemplo

El código fuente/candidato instalado incluye `examples/provider-adapters/threadcells-echo`, un paquete y manifiesto deterministas que demuestran el punto de entrada, el esquema, la validación de configuración, el ciclo de vida, el preflight y el uso no admitido. No es un proveedor de modelo y está deshabilitado de forma predeterminada. Compílalo/pruébalo de forma independiente antes de instalarlo.

Los esquemas empaquetados en `schemas/v1/adapter-manifest.schema.json` y `schemas/v1/capabilities.schema.json` son las referencias de artefactos portátiles. La validación del contrato Python sigue siendo la autoridad para el código instalado.

## La preparación debe seguir siendo veraz

Usa el nombre de ejecutable canónico del proveedor y una sonda acotada que no modifique nada. El preflight responde instalación, compatibilidad, autenticación cuando se puede detectar de forma segura y un motivo de fallo seguro para uso público. No debe afirmar que registrar el adaptador hace disponible una CLI.

Las API de registro, Settings y Spawn Agent proyectan este mismo resultado. Añade cobertura que demuestre que se deshabilita un comando no instalado, que un fallo de autenticación se distingue de la ausencia y que un proveedor instalado con autenticación genuinamente imposible de conocer sigue etiquetado como no verificado.

## El uso debe seguir siendo veraz

Prefiere un evento estructurado nativo del proveedor al análisis de texto de terminal. Registra solo los campos que emite el proveedor, conserva la identidad del punto de control acumulado y haz que reinicio/repetición sean idempotentes. Nunca conviertas una métrica no disponible en cero ni estimes el coste a partir de tokens sin un contrato explícito del proveedor.

## Lista de comprobación de revisión

- ID de adaptador, versión, nombre mostrado y esquema de configuración estables.
- Ningún campo de ejecutable, shell, argumento, entorno o secreto sin procesar seleccionable por quien llama.
- Preflight acotado sin mutación de ajustes ni autenticación.
- Capacidades admitidas/condicionales/no admitidas declaradas honestamente.
- Pruebas de ciclo de vida para inicio, estado, cancelación y fallo recuperable.
- Pruebas de uso exactas cuando se admite telemetría.
- Coherencia entre registro/Settings/Spawn.
- Errores seguros para uso público que no contienen credenciales ni rutas privadas.
