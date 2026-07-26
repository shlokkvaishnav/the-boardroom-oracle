/*
 * Production entrypoint.
 *
 * `vite build` emits two halves: `dist/client` (hashed static assets) and
 * `dist/server/server.js` (a fetch handler — our src/server.ts wrapper around
 * TanStack Start's SSR entry). In the upstream Lovable config, nitro glued
 * those together and produced a listening server. We dropped nitro along with
 * its Cloudflare target, so this file does the same job in ~40 lines: serve a
 * static file if one matches, otherwise hand the request to SSR.
 *
 * Bun is the runtime, so `export default { fetch }` from the build is called
 * directly — no adapter shim needed.
 */
import ssr from "./dist/server/server.js";

const CLIENT_DIR = new URL("./dist/client/", import.meta.url);
const port = Number(process.env.PORT ?? 3000);

/**
 * Resolve a request path inside dist/client, or null if it escapes the
 * directory. `new URL` normalises `..` segments, so the prefix check below is
 * what actually stops traversal — decoding first means `%2e%2e` is caught too.
 */
function resolveAsset(pathname: string): URL | null {
  let decoded: string;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null; // malformed percent-encoding
  }
  const candidate = new URL(`.${decoded}`, CLIENT_DIR);
  return candidate.href.startsWith(CLIENT_DIR.href) ? candidate : null;
}

Bun.serve({
  port,
  hostname: "0.0.0.0",
  idleTimeout: 120,
  async fetch(request) {
    const { pathname } = new URL(request.url);

    if (pathname !== "/") {
      const asset = resolveAsset(pathname);
      const file = asset && Bun.file(asset);
      if (file && (await file.exists())) {
        // Everything under /assets/ is content-hashed by Vite, so it is safe
        // to cache forever. Other static files (favicon) get a short TTL.
        const immutable = pathname.startsWith("/assets/");
        return new Response(file, {
          headers: {
            "cache-control": immutable
              ? "public, max-age=31536000, immutable"
              : "public, max-age=3600",
          },
        });
      }
    }

    return ssr.fetch(request, {}, {});
  },
});

console.log(`boardroom-oracle frontend listening on http://0.0.0.0:${port}`);
