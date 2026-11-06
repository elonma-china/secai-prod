import { Effect, Schema } from "effect"
import DESCRIPTION from "./screenshot.txt"
import * as Tool from "./tool"
import path from "path"

const SCREENSHOT_TOOLS = [
  {
    name: "firefox",
    check: "firefox",
    cmd: (url: string, out: string) => ["firefox", "--headless", "--screenshot", out, url],
  },
  {
    name: "chromium",
    check: "chromium",
    cmd: (url: string, out: string) => ["chromium", "--headless", "--disable-gpu", "--no-sandbox", `--screenshot=${out}`, url],
  },
  {
    name: "chromium-browser",
    check: "chromium-browser",
    cmd: (url: string, out: string) => ["chromium-browser", "--headless", "--disable-gpu", "--no-sandbox", `--screenshot=${out}`, url],
  },
  {
    name: "google-chrome",
    check: "google-chrome",
    cmd: (url: string, out: string) => ["google-chrome", "--headless", "--disable-gpu", "--no-sandbox", `--screenshot=${out}`, url],
  },
  {
    name: "cutycapt",
    check: "cutycapt",
    cmd: (url: string, out: string) => ["cutycapt", `--url=${url}`, `--out=${out}`],
  },
  {
    name: "wkhtmltoimage",
    check: "wkhtmltoimage",
    cmd: (url: string, out: string) => ["wkhtmltoimage", "--quiet", url, out],
  },
] as const

export const Parameters = Schema.Struct({
  url: Schema.String.annotate({
    description: "URL to screenshot (e.g. http://10.10.10.5:8080/admin)",
  }),
  output_path: Schema.optional(Schema.String).annotate({
    description: "Output file path. Defaults to /tmp/screenshot-<timestamp>.png",
  }),
  timeout: Schema.optional(Schema.Number).annotate({
    description: "Timeout in seconds (default 30)",
  }),
})

export const ScreenshotTool = Tool.define(
  "screenshot",
  Effect.gen(function* () {
    return {
      description: DESCRIPTION,
      parameters: Parameters,
      execute: (
        params: { url: string; output_path?: string; timeout?: number },
        _ctx: Tool.Context,
      ) =>
        Effect.gen(function* () {
          const ts = Date.now()
          const outputPath = params.output_path ?? `/tmp/screenshot-${ts}.png`
          const timeoutMs = (params.timeout ?? 30) * 1000

          let availableTool: { name: string; cmd: (url: string, out: string) => readonly string[] } | undefined
          for (const tool of SCREENSHOT_TOOLS) {
            const found = yield* Effect.tryPromise(() =>
              Bun.spawn(["which", tool.check], { stdout: "pipe", stderr: "pipe" }).exited,
            ).pipe(Effect.orElseSucceed(() => 1))
            if (found === 0) {
              availableTool = tool
              break
            }
          }

          if (!availableTool) {
            return {
              title: "No screenshot tool found",
              metadata: {},
              output: [
                "No headless screenshot tool found. Install one of:",
                "  apt install -y firefox-esr    # Kali/Debian",
                "  apt install -y chromium       # Ubuntu",
                "  apt install -y cutycapt       # lightweight Qt-based",
                "  apt install -y wkhtmltopdf    # includes wkhtmltoimage",
                "  brew install --cask firefox   # macOS",
              ].join("\n"),
            }
          }

          const cmdArgs = availableTool.cmd(params.url, outputPath)
          const proc = Bun.spawn([...cmdArgs], {
            stdout: "pipe",
            stderr: "pipe",
            env: { ...process.env, DISPLAY: process.env.DISPLAY ?? ":99" },
          })

          const exited = yield* Effect.tryPromise(() =>
            Promise.race([
              proc.exited,
              new Promise<number>((_, reject) =>
                setTimeout(() => {
                  proc.kill()
                  reject(new Error("timeout"))
                }, timeoutMs),
              ),
            ]),
          ).pipe(Effect.orElseSucceed(() => -1))

          if (exited !== 0) {
            const stderr = yield* Effect.tryPromise(() => new Response(proc.stderr).text()).pipe(
              Effect.orElseSucceed(() => ""),
            )
            return {
              title: "Screenshot failed",
              metadata: {},
              output: `Screenshot failed (exit ${exited}) using ${availableTool.name}.\n${stderr.slice(0, 500)}`,
            }
          }

          const exists = yield* Effect.tryPromise({
            try: () => Bun.file(outputPath).exists(),
            catch: () => new Error("stat failed"),
          }).pipe(Effect.orElseSucceed(() => false))

          if (!exists) {
            return {
              title: "Screenshot empty",
              metadata: {},
              output: `Screenshot command succeeded but output file is empty or missing: ${outputPath}`,
            }
          }

          return {
            title: `Screenshot: ${path.basename(outputPath)}`,
            metadata: {},
            output: `Screenshot saved to ${outputPath} (using ${availableTool.name}).\nView it with: xdg-open ${outputPath}`,
          }
        }).pipe(Effect.orDie),
    }
  }),
)
