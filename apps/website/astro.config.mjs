import sitemap from "@astrojs/sitemap";
import { defineConfig } from "astro/config";

// diony.ai is registered at Vercel. DNS points at the Hetzner server.
// This site is the static build; it is not hosted on Vercel.
const site = "https://diony.ai";

export default defineConfig({
  site,
  output: "static",
  trailingSlash: "ignore",
  integrations: [sitemap()],
  build: {
    // External stylesheets keep the edge Content-Security-Policy on style-src 'self'.
    inlineStylesheets: "never",
  },
  vite: {
    build: {
      assetsInlineLimit: 0,
    },
  },
});
