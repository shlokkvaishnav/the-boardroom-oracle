/*
 * Explicit replacement for `@lovable.dev/vite-tanstack-config`, which the
 * Lovable export used as an opaque wrapper. Every plugin and option below was
 * read out of that package's published dist (v2.7.7) so nothing it set is
 * silently lost; see the notes on each block for what was deliberately dropped.
 *
 * Plugin order is load-bearing and matches the wrapper's:
 *   tailwindcss -> tsConfigPaths -> tanstackStart -> viteReact
 */
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

const srcDir = fileURLToPath(new URL("./src", import.meta.url));

export default defineConfig(({ mode }) => {
  // Vite injects VITE_* into import.meta.env natively, but the wrapper also
  // defined them explicitly so they survive into the SSR/server environment.
  // Kept for parity — `client.ts` reads import.meta.env.VITE_BACKEND_URL on
  // both sides of the render.
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const envDefine = Object.fromEntries(
    Object.entries(env).map(([key, value]) => [`import.meta.env.${key}`, JSON.stringify(value)]),
  );

  return {
    define: envDefine,
    resolve: {
      alias: { "@": srcDir },
      // Vite 8 resolves tsconfig `paths` natively; the wrapper's
      // vite-tsconfig-paths plugin is redundant here and warns if kept.
      tsconfigPaths: true,
      // Prevents a second copy of React or the query client being pulled in
      // through a transitive dep — the classic "invalid hook call" / two
      // QueryClientProvider contexts failure.
      dedupe: [
        "react",
        "react-dom",
        "react/jsx-runtime",
        "react/jsx-dev-runtime",
        "@tanstack/react-query",
        "@tanstack/query-core",
      ],
    },
    optimizeDeps: {
      include: [
        "react",
        "react-dom",
        "react-dom/client",
        "react/jsx-runtime",
        "react/jsx-dev-runtime",
      ],
    },
    // Port 8080 is not arbitrary: the backend's ALLOWED_ORIGINS default is
    // localhost:3000,5173,8080, so moving it breaks CORS on the REST calls.
    server: { host: "::", port: 8080 },
    plugins: [
      tailwindcss(),
      tanstackStart({
        // Route the bundled server entry through src/server.ts, which unwraps
        // h3-swallowed 500s into a real error page.
        server: { entry: "server" },
        importProtection: {
          behavior: "error",
          client: { files: ["**/server/**"], specifiers: ["server-only"] },
        },
      }),
      viteReact(),
    ],
  };
});
