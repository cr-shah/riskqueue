import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const files = {
  "index.html": "text/html; charset=utf-8",
  "app.js": "text/javascript; charset=utf-8",
  "domain.js": "text/javascript; charset=utf-8",
  "styles.css": "text/css; charset=utf-8",
  "data.json": "application/json",
  "favicon.svg": "image/svg+xml",
};
createServer(async (req, res) => {
  const name =
    new URL(req.url ?? "/", "http://localhost").pathname.slice(1) ||
    "index.html";
  if (!Object.hasOwn(files, name)) {
    res.writeHead(404);
    res.end("Not found");
    return;
  }
  try {
    const content = await readFile(resolve("dist/site", name));
    res.writeHead(200, {
      "Content-Type": files[name],
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    res.end(content);
  } catch {
    res.writeHead(503);
    res.end("Run npm run build before starting the preview.");
  }
}).listen(4173, "127.0.0.1", () =>
  console.log("RiskQueue preview: http://127.0.0.1:4173/"),
);
