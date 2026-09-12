import { writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const verificationFile = process.env.GOOGLE_SITE_VERIFICATION_FILE?.trim()

if (verificationFile) {
  if (!/^google[A-Za-z0-9_-]+\.html$/.test(verificationFile)) {
    throw new Error('GOOGLE_SITE_VERIFICATION_FILE must be an exact Google verification filename')
  }
  const out = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'out')
  await writeFile(path.join(out, verificationFile), `google-site-verification: ${verificationFile}\n`, 'utf8')
  console.log(JSON.stringify({ googleVerificationFile: verificationFile }))
}
