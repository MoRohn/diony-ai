# Build the static site and serve it. TLS stays on the shared Caddy proxy.
FROM node:22-alpine AS build

WORKDIR /src
ENV ASTRO_TELEMETRY_DISABLED=1

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

RUN apk add --no-cache wget

COPY deploy/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
