---
slug: release-process
source: docs/RELEASE_PROCESS.md
source_sha256: sha256:66966038765eab40e9e11f4cbd81cc126fc199a63e95390194c3d88dbdd598b6
---
# Proceso de lanzamiento

Genere un candidato local aislado a partir de un árbol limpio y confirmado con `scripts/build_local_candidate.py --output <new-directory>`. Empaqueta los Docs/UI generados y un wheel local. Verifique `SHA256SUMS`, inspeccione `candidate-manifest.json`, `sbom.cdx.json` y `EVIDENCE.md`, y después realice la instalación limpia documentada usando un prefijo nuevo. Publicar una etiqueta, rama remota, paquete, imagen o lanzamiento público nunca es una acción ordinaria de implementación.

## Lista de verificación del lanzamiento

1. Termine la implementación y una revisión integrada independiente.
2. Ejecute pruebas focalizadas y un contorno significativo de compilación de producción/navegador.
3. Ejecute `git diff --check` y la auditoría de la superficie pública.
4. Confirme el árbol exacto aceptado.
5. Genere el candidato a partir de ese commit, nunca desde un worktree con cambios sin confirmar.
6. Verifique el manifiesto, las sumas de comprobación, el SBOM, la identidad de compilación, las rutas de Docs y la instalación limpia.
7. Conserve el runtime anterior y una copia de seguridad de la base de datos antes de la promoción local.
8. Considere cualquier push, etiqueta, paquete, imagen o lanzamiento público como una acción independiente aprobada por el propietario.

La evidencia del lanzamiento prueba qué se probó y empaquetó; por sí sola no aprueba la publicación ni certifica todas las propiedades de licencia o seguridad de las dependencias.

## Distribución OCI del lanzamiento

Los lanzamientos alpha publicados y aprobados también cuentan con un artefacto público de distribución OCI en `ghcr.io/iunknown404i/threadcells-release-bundle`. Contiene el archivo de lanzamiento verificado, el wheel de Python, inventarios de sumas de comprobación, el manifiesto del candidato, el SBOM y los metadatos del paquete de lanzamiento para una etiqueta de lanzamiento y una revisión de código fuente exactas.

Este paquete es un conjunto de distribución, no una imagen Docker ni un entorno de despliegue en contenedor compatible. Tras verificar sus sumas de comprobación, use el proceso normal de instalación y despliegue de candidatos; no intente ejecutar el artefacto OCI como un servicio ThreadCells.

`.github/workflows/publish-release-bundle.yml` publica tras un GitHub Release aprobado o mediante un envío explícito de backfill. Acepta etiquetas anotadas `v0.X.Y-alpha` con un prerelease existente que no sea borrador, conserva compatibilidad con las etiquetas históricas e inmutables `v0.X.Y-alpha.N`, reconstruye y verifica el código fuente exacto etiquetado, rechaza reemplazar una etiqueta de versión que no coincida y actualiza únicamente `latest-alpha`. ThreadCells no publica una etiqueta `latest` sin calificador durante la vista previa técnica.

## Convención de la línea de versiones

ThreadCells sigue el orden normal de prerelease de SemVer. Durante la vista previa alpha, cada nueva publicación avanza la versión semántica normal y mantiene `alpha` como etapa de prerelease. El empaquetado de Python normaliza un alpha sin sufijo, como `v0.3.3-alpha`, a `0.3.3a0`.

- `v0.1.0-alpha.1` fue el primer lanzamiento alpha público.
- `v0.1.0-alpha.2` es una vista previa técnica publicada e inmutable.
- `v0.2.0-alpha.1` es la línea de lanzamiento consolidada de multilingüismo y fiabilidad.
- `v0.3.0-alpha.1` añade coherencia de ciclo de vida, orden de creación duradero, Full Cleanup y una política de enrutamiento sistémica.
- `v0.3.0-alpha.2` corrige la entrega de Workflow Composer y hace definitivo el cierre del terminal para la autoridad de flujo ejecutable.
- `v0.3.3-alpha` añade localización en inglés y ruso a la interfaz autenticada y un único corpus canónico de Docs por idioma para la aplicación y el sitio público.
- Una publicación alpha posterior incrementa deliberadamente la versión semántica y mantiene la etapa `alpha` sin sufijo numérico.

Nunca mueva una etiqueta existente. Los cambios de gobierno del repositorio por sí solos no desencadenan un aumento de versión ni un lanzamiento. Actualice todas las superficies canónicas que contienen versiones de forma conjunta solo cuando el siguiente contorno de implementación significativo esté listo para publicarse.
