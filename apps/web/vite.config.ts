import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';
import legacy from '@vitejs/plugin-legacy';

export default defineConfig({
  // Releases are served from /current/, while development uses /. Relative
  // asset URLs work in both locations.
  base: './',
  plugins: [
    svelte(),
    legacy({
      targets: ['Safari >= 8'],
      renderLegacyChunks: true,
      modernPolyfills: true
    }),
    {
      name: 'classic-browser-entry',
      // Development uses Vite's module client and source entry. Only rewrite
      // the generated production HTML for the legacy Qt WebKit kiosk.
      apply: 'build',
      enforce: 'post',
      transformIndexHtml(html: string) {
        // The target cannot parse module scripts or the nomodule/System.import
        // bootstrap emitted by @vitejs/plugin-legacy. Load only its classic
        // polyfills and legacy application bundle.
        return html
          .replace(/\s*<script type="module"[^>]*>[\s\S]*?<\/script>/g, '')
          .replace(/\s*<script nomodule>[\s\S]*?<\/script>/g, '')
          .replace(/\s*<script nomodule[^>]*id="vite-legacy-polyfill" src="([^"]+)"><\/script>/g, '<script src="$1"></script>')
          .replace(/\s*<script nomodule[^>]*id="vite-legacy-entry" data-src="([^"]+)"[^>]*>[\s\S]*?<\/script>/g, '<script src="$1"></script>');
      }
    }
  ],
  // The Brewie kiosk uses an older Qt WebKit which does not execute ES
  // module scripts. Produce one browser script and emit it as a classic
  // script rather than the Vite default <script type="module"> entrypoint.
  build: {
    target: 'es2015',
    modulePreload: false
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8081'
    }
  }
});
