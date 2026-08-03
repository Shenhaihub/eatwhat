import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));
const port = Number(process.env.PORT || 4173);
const mime = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8"
};

createServer(async (request, response) => {
  const pathname = new URL(request.url || "/", "http://127.0.0.1").pathname;
  const safeName = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  if (safeName.includes("..")) {
    response.writeHead(400).end("Bad request");
    return;
  }
  try {
    const data = await readFile(join(root, safeName));
    response.writeHead(200, { "Content-Type": mime[extname(safeName)] || "application/octet-stream" });
    response.end(data);
  } catch {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Not found");
  }
}).listen(port, "127.0.0.1", () => {
  console.log(`EatWhat prototype: http://127.0.0.1:${port}`);
});
