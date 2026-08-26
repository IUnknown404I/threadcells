import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import ts from 'typescript'

const sourceRoot = path.resolve('src')
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

assert.deepEqual(violations, [], `Unlocalized authenticated UI strings:\n${JSON.stringify(violations, null, 2)}`)
console.log(JSON.stringify({
  files: files.length,
  intentionalSurvivors: survivors,
  categories: ['product and organization names', 'contributor attribution', 'external labels', 'literal commands'],
}))
