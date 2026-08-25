import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import ts from 'typescript'

const root = path.resolve(process.cwd())
const pagePath = path.join(root, 'app', 'page.tsx')
const pageSource = await readFile(pagePath, 'utf8')
const page = ts.createSourceFile(pagePath, pageSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const expected = new Set()

function collectCalls(node) {
  if (
    ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 't' &&
    node.arguments.length === 2 && node.arguments.every(ts.isStringLiteral)
  ) expected.add(node.arguments[0].text)
  ts.forEachChild(node, collectCalls)
}
collectCalls(page)
assert.equal(expected.size, 96, 'canonical landing key count changed; update every locale deliberately')

const localeModules = [
  { locale: 'zh-CN', exportName: 'zhCNLanding' },
  { locale: 'es', exportName: 'esLanding' },
  { locale: 'pt-BR', exportName: 'ptBRLanding' },
  { locale: 'de', exportName: 'deLanding' },
  { locale: 'ja', exportName: 'jaLanding' },
]
const intentionallyStable = new Set(['SUPERVISOR', 'WORKER', 'REVIEWER', 'WORKFLOW', 'resident', 'OPEN SOURCE'])
const placeholder = /\b(?:TODO|TBD|TRANSLATE)\b|\b[Ll][Oo][Rr][Ee][Mm]\s+[Ii][Pp][Ss][Uu][Mm]\b/

for (const { locale, exportName } of localeModules) {
  const file = path.join(root, 'lib', 'landing', `${locale}.ts`)
  const source = await readFile(file, 'utf8')
  assert(pageSource.includes(`import { ${exportName} } from '@/lib/landing/${locale}'`), `${locale} landing module is not imported`)
  assert(pageSource.includes(`locale === '${locale}' ? ${exportName}`), `${locale} landing module is not registered`)
  assert(!placeholder.test(source), `${locale} landing contains placeholder text`)
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const values = new Map()
  function collectMap(node) {
    if (
      ts.isPropertyAssignment(node) && ts.isStringLiteral(node.name) && ts.isStringLiteral(node.initializer) &&
      node.parent?.parent && ts.isPropertyAssignment(node.parent.parent) && node.parent.parent.name.getText(tree) === 'strings'
    ) values.set(node.name.text, node.initializer.text)
    ts.forEachChild(node, collectMap)
  }
  collectMap(tree)
  const missing = [...expected].filter(key => !values.has(key))
  const unknown = [...values.keys()].filter(key => !expected.has(key))
  assert.deepEqual(missing, [], `${locale} landing is missing canonical keys`)
  assert.deepEqual(unknown, [], `${locale} landing has unknown keys`)
  for (const [key, value] of values) {
    assert(value.trim(), `${locale} landing has an empty value for ${key}`)
    assert(value !== key || intentionallyStable.has(key), `${locale} landing silently copied English: ${key}`)
  }
}

const localeAuthority = await readFile(path.join(root, 'lib', 'locales.ts'), 'utf8')
assert(localeAuthority.includes('AWS не спонсирует и не участвует в нём.'), 'approved Russian downstream sentence changed')
console.log(JSON.stringify({ locales: localeModules.length + 2, translatedLandingModules: localeModules.length, canonicalKeys: expected.size }))
