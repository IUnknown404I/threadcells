---
slug: localization
source: docs/LOCALIZATION.md
source_sha256: sha256:b3b5c92689f7e752f2aa7f508f22a9dba9007f076b16be56a7c10c02bcd548dc
---
# Guía de localización

El inglés es la autoridad canónica para la documentación pública de ThreadCells, el README raíz y las afirmaciones sobre el producto. Una traducción puede mejorar la redacción natural, pero no debe omitir ni inventar comportamiento, debilitar un límite de seguridad, cambiar un límite ni alterar un comando.

## Modelo de locale

Los locales de lanzamiento son `en`, `ru`, `zh-CN`, `es`, `pt-BR`, `de` y `ja`. El Markdown canónico permanece en la fuente nombrada por `docs/DOCS_MANIFEST.json`. Cada documento que no está en inglés se encuentra en `docs/i18n/LOCALE/SLUG.md` y registra:

```yaml
---
slug: overview
source: docs/OVERVIEW.md
source_sha256: sha256:EXACT_ENGLISH_SOURCE_HASH
---
```

Los slugs, el orden del manifiesto y la pertenencia a la navegación se comparten entre locales. No cree un segundo manifiesto de Docs ni un renderizador específico de locale.

## Actualizar una traducción

1. Actualice y acepte primero el documento canónico en inglés.
2. Traduza cada afirmación y encabezado sin cambiar código ni identificadores.
3. Actualice `source_sha256` a partir de los bytes exactos de la fuente canónica.
4. Ejecute `python3 scripts/validate_localizations.py`.
5. Compile el sitio web e inspeccione las rutas afectadas en anchos de escritorio, tableta y móvil.

El validador rechaza slugs traducidos ausentes, obsoletos, desconocidos, duplicados o no coincidentes. Un locale compatible no debe publicar silenciosamente una traducción antigua después de que cambie el inglés.

## Agregar un locale

Agregue el locale una vez en `website/lib/locales.ts`, proporcione todos sus metadatos de página de destino/UI, añada un documento traducido para cada slug del manifiesto, añada su README localizado y extienda las comprobaciones deterministas de rutas/navegador. Conserve el mismo slug público al cambiar de idioma.

Agregar un locale futuro como `fr` o `ko` debe ser un cambio de contenido acotado. No debe requerir otra aplicación, manifiesto ni arquitectura de Docs.

## Texto técnico

Mantenga exactamente estos elementos salvo que cambie la fuente canónica en inglés:

- bloques de código delimitados y comandos de shell;
- identificadores de código en línea;
- rutas de API, claves de configuración, variables de entorno, códigos de razón, ID de perfil/proveedor, nombres de paquetes y rutas de archivos;
- nombres de productos y proveedores como ThreadCells, Codex, Claude Code, Git, Git worktree y tmux;
- destinos de enlaces Markdown y rutas de medios.

Traduzca con naturalidad las explicaciones que rodean esos valores. Evite calcos literales que dificulten la orientación para desarrolladores.

## Archivos README

`README.md` es el inglés canónico. Cada README localizado sigue la misma estructura de secciones, enlaza a la misma evidencia y comienza con el selector compacto de siete idiomas. Resalte el idioma actual en negrita y use enlaces relativos al repositorio para los otros seis.

## Aceptación visual

Las traducciones no necesitan saltos de línea ni alturas de sección idénticos. Deben conservar la jerarquía, tipografía legible, CTA funcionales, medios, tablas, bloques de código, comportamiento de encabezado/pie de página y ausencia total de desbordamiento horizontal. Preste especial atención a la expansión del alemán, el ajuste del ruso, la navegación en español y portugués y el salto de línea en chino/japonés.

Sigue siendo necesaria una revisión semántica por parte de un lector fluido de contenido orientado a desarrolladores. Superar las comprobaciones de Markdown, hash, rutas y navegador demuestra actualidad estructural; no demuestra la calidad de la traducción.
