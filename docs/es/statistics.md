---
slug: statistics
source: docs/STATISTICS.md
source_sha256: sha256:ca9ce387ff845fb61aba3bc22c45084a825764e2ce7e156d6963e152e490de2b
---

# Estadísticas y uso del proveedor

Statistics resume el uso que emiten realmente las CLI de proveedor compatibles. Ayuda a responder qué sesiones, perfiles, proyectos y proveedores consumieron tokens de modelo; no es un libro mayor de facturación ni inventa valores que faltan.

## Qué significan los números

Para Codex, ThreadCells registra los contadores acumulados nativos del proveedor disponibles en la telemetría de rollout:

- tokens de entrada;
- tokens de entrada en caché;
- tokens de salida;
- tokens de razonamiento;
- tokens totales.

La entrada en caché sigue visible por separado. No se vuelve a añadir silenciosamente como entrada nueva. Una métrica que el proveedor no informó aparece como **No informado**, no como un cero engañoso.

Las tablas predeterminadas omiten los tokens de escritura de caché porque ningún adaptador actual los expone como métrica admitida significativa. La API normalizada conserva un campo opcional de compatibilidad para que un adaptador futuro pueda añadir soporte veraz sin una migración de base de datos.

La información de crédito, precio y coste del proveedor solo se muestra cuando el adaptador proporciona un valor admitido y autorizado. ThreadCells no estima facturas a partir de totales de tokens.

## Cuándo aparece el uso

El uso se recopila mientras se ejecuta una sesión activa y se guarda de forma duradera. No es necesario eliminar, retirar ni limpiar una sesión para que contribuya a Statistics. Las sesiones completadas pero retenidas siguen contando.

Codex emite instantáneas acumuladas. ThreadCells establece puntos de control de esas instantáneas y actualiza el mismo registro de uso canónico, de forma que polling, reinicio, repetición o reanudación no cuenten los mismos tokens dos veces.

## Leer la página

Empieza por los totales globales y después usa las tablas de dimensiones para localizar el uso por terminal, sesión, proyecto, proveedor o perfil. Los totales usan los mismos registros normalizados que las vistas detalladas.

Ejemplo de investigación:

1. Observa un aumento en los tokens globales de salida.
2. Abre la dimensión de sesiones para identificar la sesión que contribuye.
3. Compara su proyecto, proveedor y perfil.
4. Abre Agents para inspeccionar el terminal correspondiente y el resultado duradero.

## Datos históricos

Las actualizaciones pueden recuperar uso histórico solo cuando la evidencia nativa del proveedor retenida se puede asociar de forma determinista a una sesión de ThreadCells. Los datos fuente ambiguos o ausentes siguen siendo desconocidos. Una reparación es idempotente: ejecutarla otra vez no debe crear un registro duplicado.

El análisis heredado de terminales de mejor esfuerzo puede permanecer en bases de datos antiguas por procedencia. Cuando existe un registro exacto nativo del proveedor, el registro exacto sustituye la aproximación heredada en los totales visibles.

## Solución de problemas

- **Falta una sesión activa:** actualiza la página, verifica que el proveedor admita recopilar uso y confirma que el rollout del proveedor siga siendo legible para la cuenta de servicio.
- **Un campo dice No informado:** el proveedor no proporcionó esa métrica. No lo interpretes como cero.
- **Los totales parecen duplicados tras reiniciar:** compara las dimensiones de sesión y terminal y conserva la base de datos para diagnóstico; una repetición debe actualizar un punto de control, no insertar un segundo total acumulado.
- **La facturación difiere:** usa el propio sistema de facturación del proveedor como autoridad de facturación. ThreadCells informa telemetría operativa.

Para capacidad, no para contabilidad de tokens, consulta [Capacidad y modelo de recursos](RESOURCE_MODEL.md).
