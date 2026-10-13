# Deploy Diony.ai on Hetzner

The marketing site is a static Astro build. A container serves the files on an internal Docker network. A shared Caddy process on the same Hetzner server terminates HTTPS for `diony.ai`, `www.diony.ai`, and any other sites on that machine.

The domain is registered at Vercel. Vercel is DNS only. The site does not run on Vercel or Netlify.

The server IPv4 is `2.29.59.0`. An IPv6 address, the ACME mailbox, and certificates are not in this folder. Replace every `<PLACEHOLDER>` before use.

## What you need

- A Hetzner server you already control, with Docker and the Docker Compose plugin.
- Ports 80 and 443 open to the internet (Caddy’s HTTP-01 challenge and HTTPS).
- This host’s IPv4, `2.29.59.0`, and its IPv6 address if it has one.
- A mailbox address for Let’s Encrypt expiry notices.

## 1. DNS at Vercel

In the Vercel dashboard, open the `diony.ai` domain and edit DNS. Point the names at the Hetzner server. Remove any record that still sends `diony.ai` or `www.diony.ai` to Vercel hosting, or browsers will never reach this machine.

| Type | Name | Value |
|---|---|---|
| A | `@` | `2.29.59.0` |
| AAAA | `@` | `<HETZNER_IPV6>` |
| A | `www` | `2.29.59.0` |
| AAAA | `www` | `<HETZNER_IPV6>` |

Skip the AAAA rows when the server has no IPv6 address. Use the same addresses for every site on this server; Caddy chooses the site by the `Host` header.

Leave the records time to publish before expecting a certificate. Caddy will retry the challenge.

## 2. Shared proxy, once per server

On the server:

```bash
docker network create web
sudo mkdir -p /opt/caddy
sudo cp Caddyfile.example /opt/caddy/Caddyfile
sudo cp caddy-compose.example.yml /opt/caddy/docker-compose.yml
sudo cp .env.example /opt/caddy/.env
```

Edit `/opt/caddy/.env` and set `ACME_EMAIL` to a real mailbox. Edit `/opt/caddy/Caddyfile` when you add another hostname: duplicate the commented site block and point it at that site’s container name. Do not start a second process on ports 80 or 443.

```bash
cd /opt/caddy
docker compose up -d
```

`www.diony.ai` redirects to `https://diony.ai`. Both names get certificates.

## 3. Build and start this site

From a checkout of this repository, on the server or on a machine that can push the image:

```bash
cd apps/website
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d
```

The image runs `npm ci` and `npm run build`, then nginx serves `dist/` on container port 80. The compose file does not publish that port on the host. Caddy reaches it as `diony-website` on the `web` network.

A local smoke test, bound to loopback only:

```bash
docker run --rm -p 127.0.0.1:8080:80 diony-website:local
```

Open `http://127.0.0.1:8080/`. Stop it before you rely on Caddy, and do not publish 8080 on a public interface.

## 4. Update

```bash
cd apps/website
git pull
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d
```

Caddy keeps its certificates in the `caddy_data` volume. Replacing the website container does not reissue them.

## Layout on a multi-site server

| Piece | How many | Public ports |
|---|---|---|
| Docker network `web` | one | none |
| Caddy | one | 80, 443 |
| This site (`diony-website`) | one | none |
| Each other website | one container | none |

Firewall: allow 22 (your own SSH policy), 80, and 443. Nothing else about this site needs to be reachable.

## Files

| File | Role |
|---|---|
| `Dockerfile` | Multi-stage build: Node 22 compiles the site, nginx 1.27 serves it |
| `nginx.conf` | Static files, cache headers, 404 page |
| `security-headers.conf` | Headers nginx would otherwise drop inside a `location` |
| `docker-compose.yml` | This site only, joined to the external `web` network |
| `Caddyfile.example` | Shared proxy. `diony.ai`, `www.diony.ai`, and a commented second site |
| `caddy-compose.example.yml` | One Caddy service for the whole server |
| `.env.example` | `ACME_EMAIL=replace-me@example.com` |
