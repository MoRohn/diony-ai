# Diony AI website

One cinematic page for [diony.ai](https://diony.ai). Static [Astro 5](https://astro.build/) build. It does not import the desktop apps.

The line on the page is **Own your studio | Produce at cost**. **Own it** is the only action. It is in the header, the opening frame, and the last frame, and it goes to `https://blerbznews.gumroad.com/l/Diony`.

Copy on the reel is only what the product already does: a desktop studio, one camera for the whole film (Grok Imagine or Google Flow), a price before anything rolls, no GPU, keys and vault on the machine, and a master with subtitles, an edit list, a QC packet, and a receipt. There is no download page, no price figure, and no customer list.

Visual tokens follow Blueprint 15: void `#16191F`, parchment `#F4F0E6`, crimson `#C41E3A`, amber `#E5AC48`. The mark is `Diony-AI-logo.png`. Motion is transform and opacity. `prefers-reduced-motion: reduce` stops it.

## Develop

Node.js 20 or newer. The Docker image uses 22.

```bash
cd apps/website
npm ci
npm run dev
```

Dev server: `http://127.0.0.1:4321/`.

```bash
npm run build
npm run preview
```

## Deploy

The site is a container on the founder’s Hetzner server, behind one Caddy instance that also fronts the other sites on that machine. Steps, the Caddyfile, and DNS are in [`deploy/README.md`](deploy/README.md).

From `apps/website`:

```bash
docker network create web   # once per server
docker compose -f deploy/docker-compose.yml build
docker compose -f deploy/docker-compose.yml up -d
```

In Vercel’s DNS for `diony.ai`, point the names at the server and remove any record that still sends the domain to Vercel hosting:

| Type | Name | Value |
|---|---|---|
| A | `@` | `2.29.59.0` |
| AAAA | `@` | `<HETZNER_IPV6>` |
| A | `www` | `2.29.59.0` |
| AAAA | `www` | `<HETZNER_IPV6>` |

`2.29.59.0` is the Hetzner host. `<HETZNER_IPV6>` is still a placeholder: add those rows only when the server has an IPv6 address. `www.diony.ai` redirects to `https://diony.ai`.

## Still needs the founder

1. A license price, if one should appear on the page. The Gumroad link is the door. The repo does not name an amount.
2. Support email, social accounts, and a counsel-reviewed privacy policy and terms. They are not on the splash.
3. IPv6 for the server, and the Let’s Encrypt mailbox in `deploy/.env.example`.
4. A designed Open Graph image. The ticket logo is the `og:image` until one exists.
