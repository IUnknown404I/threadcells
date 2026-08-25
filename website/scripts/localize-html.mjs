import { readdir, readFile, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'

const output = path.resolve(process.cwd(), 'out')
const localeLang = new Map([
  ['ru', 'ru'],
  ['zh-CN', 'zh-CN'],
  ['es', 'es'],
  ['pt-BR', 'pt-BR'],
  ['de', 'de'],
  ['ja', 'ja'],
])

async function htmlFiles(directory) {
  const files = []
  for (const entry of await readdir(directory)) {
    const candidate = path.join(directory, entry)
    if ((await stat(candidate)).isDirectory()) files.push(...await htmlFiles(candidate))
    else if (entry.endsWith('.html')) files.push(candidate)
  }
  return files
}

let changed = 0
for (const [locale, lang] of localeLang) {
  const localeRoot = path.join(output, locale)
  for (const file of await htmlFiles(localeRoot)) {
    const source = await readFile(file, 'utf8')
    if (!/<html lang="en"/.test(source)) throw new Error(`Expected root language marker in ${file}`)
    await writeFile(file, source.replace('<html lang="en"', `<html lang="${lang}"`))
    changed += 1
  }
}

if (!changed) throw new Error('No localized HTML files were emitted')
console.log(JSON.stringify({ localizedHtmlFiles: changed }))
