---
slug: issues
source: docs/ISSUES.md
source_sha256: sha256:fa980f53f7ec42635a41273a8d82bdf2da52cab760ee5da675fbc6a00792cee4
---
# Política de Issues públicos

Los GitHub Issues son el backlog público seleccionado de ThreadCells, no una transcripción de advertencias, auditorías o depuración de lanzamientos.

## Elegibilidad

Normalmente, un Issue público debe satisfacer todas estas condiciones:

- el problema u oportunidad sigue sin resolverse;
- es reproducible o está respaldado por evidencia duradera;
- tiene un impacto significativo en usuarios, proyecto, fiabilidad, documentación o mantenibilidad;
- el seguimiento público es útil y accionable para el proyecto o la comunidad;
- la divulgación pública es segura;
- el comportamiento o resultado esperado es claro; y
- se pueden expresar criterios de aceptación concretos.

La evidencia técnica duradera puede sustituir los pasos de reproducción cuando la reproducción determinista no es práctica.

Las preguntas, la resolución de problemas y la conversación abierta pertenecen a [Discussions Q&A](https://github.com/IUnknown404I/threadcells/discussions/categories/q-a). Explore propuestas tempranas centradas primero en el problema y el caso de uso en [Discussions Ideas](https://github.com/IUnknown404I/threadcells/discussions/categories/ideas). Traslade un hallazgo o propuesta a Issues solo después de que esté confirmado, sea concreto, seguro para divulgar públicamente y accionable conforme a esta política.

## Qué no pertenece a los Issues públicos

No cree un Issue público meramente por:

- administración exclusiva del propietario de la cuenta o repositorio;
- administración de credenciales o trabajo de infraestructura privada;
- credenciales, secretos o detalles de seguridad que no sea seguro divulgar;
- ruido transitorio de CI, entorno, red o runner;
- hallazgos ya resueltos;
- identificadores de runtime aislados sin una clase de problema subyacente reproducible;
- advertencias que se comportan de forma segura sin un defecto demostrado;
- pulido especulativo sin un problema y resultado definidos;
- observaciones temporales de lanzamiento o depuración; o
- notas sin clasificar de una auditoría o barrido de deuda residual.

Las acciones exclusivas del propietario pertenecen al canal operativo del propietario del repositorio, no al backlog de contribuidores. Un hallazgo se convierte en un Issue público solo después de superar la compuerta de elegibilidad.

## Contenido del informe

Use el formulario de Issue correspondiente e incluya las partes útiles de esta estructura:

1. **Problema / Contexto**
2. **Impacto**
3. **Comportamiento actual**
4. **Comportamiento esperado**
5. **Reproducción o evidencia**
6. **Criterios de aceptación**
7. **No objetivos**, cuando resulte útil

Incluya información de entorno o versión solo cuando afecte al informe. Censure registros y capturas de pantalla. Nunca incluya secretos, credenciales, datos personales, mensajes privados, rutas privadas innecesarias, bases de datos de estado ni transcripciones de terminal.

Las vulnerabilidades y hallazgos sensibles de seguridad deben usar la vía privada de [SECURITY.md](../SECURITY.md), no un Issue público.

## Triaje y duplicados

Busque Issues abiertos y cerrados antes de crear uno. Los mantenedores enlazan los duplicados al Issue canónico y los cierran como duplicados en vez de dividir la discusión y la evidencia.

Use el conjunto de etiquetas útil más pequeño. `bug`, `enhancement`, `documentation`, `accessibility` y `technical-debt` describen el trabajo; `duplicate` describe el triaje. Los mantenedores pueden solicitar evidencia faltante antes de decidir si un informe reúne los requisitos.

Cierre un Issue cuando se satisfagan los criterios de aceptación, cuando duplique un Issue canónico, o como no planificado con un motivo conciso cuando esté fuera de alcance, no pueda hacerse accionable o ya no justifique el seguimiento del proyecto. Los informes ya resueltos deben indicar la evidencia que los resolvió.

## Etiquetas para contribuidores

Use `good first issue` únicamente para trabajo seguro, acotado y de baja ambigüedad, con suficiente contexto y criterios de aceptación para un nuevo contribuidor. Use `help wanted` únicamente cuando la contribución externa sea realmente bienvenida y la tarea esté lo bastante especificada.

Los límites críticos de seguridad o autenticación, el ciclo de vida y comportamiento de exactamente una vez, la seguridad destructiva, la autoridad de lanzamiento, la confianza de proveedores o límites de ejecución remota de código, las migraciones y la integridad de datos nunca son automáticamente trabajo para principiantes.
