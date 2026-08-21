import Link from 'next/link'

export default function NotFound() {
  return (
    <main className="not-found">
      <p className="eyebrow">ROUTE / NOT FOUND</p>
      <h1>This page does not exist.</h1>
      <p>Return to the ThreadCells control room.</p>
      <Link className="button button-primary" href="/">Return home</Link>
    </main>
  )
}
