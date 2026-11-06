// Smoke test: reproduce the SkillV2.load() discovery logic (packages/core/src/skill.ts)
// against the real ./skills directory and validate every skill would be loaded.
import { Glob } from "bun"
import path from "path"
import { ConfigMarkdown } from "../packages/core/src/config/markdown"

const SKILLS_DIR = path.resolve(import.meta.dir, "../skills")
const EXPECTED = [
  "recon-phase",
  "enumeration-phase",
  "vuln-assess-phase",
  "exploitation-phase",
  "playbook-methodology",
  "playbook-api",
  "playbook-social-engineering",
  "svc-mobile-ios",
]

const frontmatterSchema = (data: unknown): { name?: string; description?: string } | undefined => {
  if (typeof data !== "object" || data === null) return undefined
  const record = data as Record<string, unknown>
  if (record.name !== undefined && typeof record.name !== "string") return undefined
  if (record.description !== undefined && typeof record.description !== "string") return undefined
  return { name: record.name as string | undefined, description: record.description as string | undefined }
}

const files = (await Array.fromAsync(new Glob("**/SKILL.md").scan({ cwd: SKILLS_DIR, absolute: true, dot: true }))).map(
  (f) => path.resolve(f),
)
const loaded: { name: string; description?: string }[] = []
const rejected: string[] = []

for (const filepath of files.toSorted()) {
  const content = await Bun.file(filepath).text()
  const markdown = ConfigMarkdown.parseOption(content)
  if (!markdown) {
    rejected.push(`${filepath} — markdown parse failed`)
    continue
  }
  const frontmatter = frontmatterSchema(markdown.data)
  if (!frontmatter) {
    rejected.push(`${filepath} — invalid frontmatter schema`)
    continue
  }
  const name = frontmatter.name ?? (path.dirname(filepath) === SKILLS_DIR ? path.basename(filepath, ".md") : undefined)
  if (!name) {
    rejected.push(`${filepath} — missing name (and not a directory-based skill)`)
    continue
  }
  if (!markdown.content.trim()) {
    rejected.push(`${filepath} — empty body`)
    continue
  }
  loaded.push({ name, description: frontmatter.description })
}

const names = new Set(loaded.map((s) => s.name))
const missingDescription = loaded.filter((s) => !s.description)
const duplicates = loaded.map((s) => s.name).filter((n, i, all) => all.indexOf(n) !== i)
const missingExpected = EXPECTED.filter((n) => !names.has(n))

console.log(`Discovered: ${loaded.length} skills from ${files.length} SKILL.md files`)
console.log(`New skills present: ${EXPECTED.length - missingExpected.length}/${EXPECTED.length}`)
if (missingExpected.length) console.log("MISSING:", missingExpected)
if (rejected.length) console.log("REJECTED:", rejected)
if (missingDescription.length) console.log("NO DESCRIPTION:", missingDescription.map((s) => s.name))
if (duplicates.length) console.log("DUPLICATE NAMES:", duplicates)

const failed = rejected.length > 0 || missingExpected.length > 0 || duplicates.length > 0
console.log(failed ? "SMOKE TEST FAILED" : "SMOKE TEST PASSED")
process.exit(failed ? 1 : 0)
