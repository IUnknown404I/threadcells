import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import ts from 'typescript'

const sourceRoot = path.resolve('src')
const repositoryRoot = path.resolve('..')
const files = []

async function collect(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name)
    if (entry.isDirectory()) {
      if (entry.name !== 'test') await collect(file)
    } else if (file.endsWith('.tsx')) files.push(file)
  }
}

await collect(sourceRoot)

// Intentional first-party survivors are product/organization names, contributor
// attribution, external labels, and literal commands. They must stay canonical.
const allowed = new Map([
  ['src/App.tsx', new Set(['ThreadCells', '© 2026 ThreadCells', 'GitHub'])],
  ['src/components/ControlPlaneSettings.tsx', new Set(['· r', '· API', 'ThreadCells', 'Subaev Ruslan'])],
  ['src/components/DocsPanel.tsx', new Set(['ThreadCells'])],
  ['src/components/FlowsPanel.tsx', new Set(['cao flow add <file.md>'])],
  ['src/components/SettingsPanel.tsx', new Set(['threadcells install developer'])],
])
const visibleAttributes = new Set(['alt', 'aria-label', 'label', 'placeholder', 'title'])
const survivors = []
const violations = []
const russianOperatorTerms = /\b(?:Housekeeping|Workflows?|Ready|Exited|Providers?|Heavy|Work|turn)\b/i

for (const absoluteFile of files) {
  const relativeFile = path.relative(process.cwd(), absoluteFile)
  const source = await readFile(absoluteFile, 'utf8')
  const ast = ts.createSourceFile(relativeFile, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const inspect = node => {
    let value = null
    if (ts.isJsxText(node)) value = node.text.trim().replace(/\s+/g, ' ').replaceAll('&lt;', '<').replaceAll('&gt;', '>')
    if (ts.isJsxAttribute(node) && visibleAttributes.has(node.name.getText(ast)) && node.initializer && ts.isStringLiteral(node.initializer)) value = node.initializer.text
    if (value && /[A-Za-zА-Яа-я]/.test(value)) {
      const record = { file: relativeFile, value }
      if (allowed.get(relativeFile)?.has(value)) survivors.push(record)
      else violations.push(record)
    }
    ts.forEachChild(node, inspect)
  }
  inspect(ast)
}

const catalogPath = path.join(sourceRoot, 'i18n', 'catalogs.ts')
const catalogSource = await readFile(catalogPath, 'utf8')
const catalogAst = ts.createSourceFile(catalogPath, catalogSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
let russianCatalog = null
for (const statement of catalogAst.statements) {
  if (!ts.isVariableStatement(statement)) continue
  for (const declaration of statement.declarationList.declarations) {
    if (declaration.name.getText(catalogAst) === 'ru' && declaration.initializer && ts.isObjectLiteralExpression(declaration.initializer)) {
      russianCatalog = declaration.initializer
    }
  }
}
assert.ok(russianCatalog, 'Russian catalog object was not found')
const russianCatalogViolations = []
for (const property of russianCatalog.properties) {
  if (!ts.isPropertyAssignment(property)) continue
  const value = property.initializer
  const visibleValue = (ts.isStringLiteral(value) || ts.isNoSubstitutionTemplateLiteral(value))
    ? value.text.replace(/\{[^}]+\}/g, '')
    : ''
  if (russianOperatorTerms.test(visibleValue)) {
    russianCatalogViolations.push({ key: property.name.getText(catalogAst), value: value.text })
  }
}

function markdownProse(value) {
  return value
    .replace(/^---\n[\s\S]*?\n---\n/, '')
    .replace(/^(?:```|~~~)[\s\S]*?^(?:```|~~~)[^\n]*$/gm, '')
    .replace(/`[^`\n]*`/g, '')
    .replace(/\]\([^)]+\)/g, ']')
}

const russianDocsDirectory = path.join(repositoryRoot, 'docs', 'ru')
const russianDocsViolations = []
for (const name of (await readdir(russianDocsDirectory)).filter(name => name.endsWith('.md')).sort()) {
  const relativeFile = path.posix.join('docs', 'ru', name)
  const lines = markdownProse(await readFile(path.join(russianDocsDirectory, name), 'utf8')).split('\n')
  lines.forEach((line, index) => {
    const matches = [...line.matchAll(new RegExp(russianOperatorTerms.source, 'gi'))].map(match => match[0])
    if (matches.length) russianDocsViolations.push({ file: relativeFile, line: index + 1, terms: matches, value: line.trim() })
  })
}

assert.deepEqual(violations, [], `Unlocalized authenticated UI strings:\n${JSON.stringify(violations, null, 2)}`)
assert.deepEqual(russianCatalogViolations, [], `English operator terms in Russian catalog:\n${JSON.stringify(russianCatalogViolations, null, 2)}`)
assert.deepEqual(russianDocsViolations, [], `English operator terms in Russian Markdown prose:\n${JSON.stringify(russianDocsViolations, null, 2)}`)
console.log(JSON.stringify({
  files: files.length,
  russianDocs: (await readdir(russianDocsDirectory)).filter(name => name.endsWith('.md')).length,
  intentionalSurvivors: survivors,
  categories: ['product and organization names', 'contributor attribution', 'external labels', 'literal commands'],
}))
