# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# deps — resolved separately so editing source doesn't reinstall node_modules.
# ---------------------------------------------------------------------------
FROM oven/bun:1.3-alpine AS deps
WORKDIR /srv
COPY package.json bun.lock bunfig.toml ./
RUN --mount=type=cache,target=/root/.bun/install/cache \
    bun install --frozen-lockfile

# ---------------------------------------------------------------------------
# build — VITE_* values are compiled into the bundle, so the backend URL has to
# be known here rather than at run time. It must be the URL the *browser* will
# use (the REST + WebSocket calls happen client-side), which is why it defaults
# to localhost:8000 and not the compose service name.
# ---------------------------------------------------------------------------
FROM deps AS build
ARG VITE_BACKEND_URL=http://localhost:8000
ENV VITE_BACKEND_URL=$VITE_BACKEND_URL
COPY . .
RUN bun run build

# ---------------------------------------------------------------------------
# runtime — only the build output and production deps.
# ---------------------------------------------------------------------------
FROM oven/bun:1.3-alpine AS runtime
ENV NODE_ENV=production \
    PORT=3000
WORKDIR /srv

RUN addgroup -g 1001 app && adduser -D -u 1001 -G app app

COPY --from=deps  --chown=app:app /srv/node_modules ./node_modules
COPY --from=build --chown=app:app /srv/dist         ./dist
COPY --from=build --chown=app:app /srv/serve.ts     ./serve.ts
COPY --from=build --chown=app:app /srv/package.json ./package.json

USER app

EXPOSE 3000

# 127.0.0.1, not localhost: the image resolves `localhost` to ::1 first, but
# Bun.serve binds IPv4 only, so the IPv6 attempt is refused and the container
# is reported unhealthy while actually serving fine.
HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=10s \
  CMD wget -qO- http://127.0.0.1:3000/ >/dev/null 2>&1 || exit 1

CMD ["bun", "run", "serve.ts"]
