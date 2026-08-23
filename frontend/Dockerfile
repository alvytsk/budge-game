# Build the SPA, then hand it to Caddy. Two stages, one image: Caddy needs
# the files and nothing needs node at runtime.
FROM node:22-alpine AS build

RUN corepack enable
WORKDIR /app

# pnpm 11 quarantines any package published within the last day
# (`minimumReleaseAge`) and refuses a lockfile that names one. The committed
# lockfile is resolved on a machine whose global pnpm config sets that to 0,
# so without this the very same reviewed lockfile is rejected here and the
# image cannot be built at all whenever a dependency shipped yesterday. It is
# set in the global config rather than on the command line because `pnpm run`
# re-invokes `pnpm install` itself to check the tree is current, and that
# invocation carries none of our flags.
RUN mkdir -p /root/.config/pnpm \
 && printf 'minimumReleaseAge: 0\n' > /root/.config/pnpm/config.yaml

# The lockfile alone first, so `pnpm install` re-runs only when it changes.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

# The Caddyfile is not copied in: a Dockerfile may not read above its build
# context, and compose bind-mounts it instead (Task 2) — which also means
# editing a route does not mean rebuilding the SPA.
FROM caddy:2-alpine
COPY --from=build /app/dist /srv
