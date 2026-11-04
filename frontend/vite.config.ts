import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { minifySync } from 'rolldown/experimental';
import { resolve } from 'path';

// SECURITY: minify every JS chunk with console.*/debugger stripped so the
// browser console can never leak PHI, tokens, or internal state in production.
// Vite 8's default oxc minifier doesn't expose a drop-console toggle, so we run
// oxc's minifier ourselves (with dropConsole) and disable Vite's own pass.
function dropConsolePlugin(): Plugin {
  return {
    name: 'cg-drop-console',
    apply: 'build',
    enforce: 'post',
    renderChunk(code, chunk) {
      if (!chunk.fileName.endsWith('.js')) return null;
      const result = minifySync(chunk.fileName, code, {
        module: true,
        compress: { dropConsole: true, dropDebugger: true },
        mangle: true,
      });
      return { code: result.code, map: null };
    },
  };
}

export default defineConfig({
  plugins: [react(), dropConsolePlugin()],
  resolve: {
    alias: {
      '@': resolve(import.meta.dirname, './src'),
    },
  },
  build: {
    // Keep Vite's oxc minifier (handles JS + CSS); our post plugin re-passes JS
    // to strip console/debugger. Ship no sourcemaps so internals aren't exposed.
    sourcemap: false,
  },
  server: {
    watch: {
      usePolling: true,
      ignored: ['**/node_modules/**', '**/dist/**'],
    },
    // Proxy API + WebSocket to the ClaimGuard backend (avoids CORS in dev).
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
