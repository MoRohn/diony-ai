import { execFile } from "node:child_process";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const svgPath = resolve(root, "og/og.svg");
const pngPath = resolve(root, "public/og.png");
const work = resolve(root, ".og-render");
const fontPath = resolve(
  root,
  "node_modules/@fontsource/archivo-black/files/archivo-black-latin-400-normal.woff2",
);

const svg = await readFile(svgPath, "utf8");
const font = (await readFile(fontPath)).toString("base64");
const face = `<style><![CDATA[@font-face{font-family:"Archivo Black";src:url("data:font/woff2;base64,${font}") format("woff2");font-weight:400;font-style:normal;}]]></style>`;
const inlined = svg.replace("<defs>", `<defs>${face}`);

await mkdir(work, { recursive: true });
const htmlPath = resolve(work, "og.html");
await writeFile(
  htmlPath,
  `<!doctype html><html><head><meta charset="utf-8"><style>
    html,body{margin:0;width:1200px;height:630px;overflow:hidden;background:#16191F}
    svg{display:block;width:1200px;height:630px}
  </style></head><body>${inlined}</body></html>`,
);

const shot = resolve(work, "shot.png");
const chrome = process.env.CHROME_PATH || "google-chrome";
try {
  await execFileAsync(
    "timeout",
    [
      "25",
      chrome,
      "--headless=new",
      "--no-sandbox",
      "--disable-gpu",
      "--disable-dev-shm-usage",
      "--hide-scrollbars",
      "--force-device-scale-factor=1",
      `--user-data-dir=${resolve(work, "profile")}`,
      "--window-size=1200,630",
      `--screenshot=${shot}`,
      htmlPath,
    ],
    { timeout: 30_000 },
  );
} catch (error) {
  const code = error && typeof error === "object" && "code" in error ? error.code : undefined;
  if (code !== 124) throw error;
}

const png = await readFile(shot);
const width = png.readUInt32BE(16);
const height = png.readUInt32BE(20);
if (width !== 1200 || height !== 630) {
  throw new Error(`Expected 1200x630, got ${width}x${height}`);
}
if (png.length > 300 * 1024) {
  throw new Error(`PNG is ${png.length} bytes, over the 300 KB limit`);
}
await writeFile(pngPath, png);
await rm(work, { recursive: true, force: true });
console.log(`Wrote ${pngPath} (${png.length} bytes, ${width}x${height})`);
