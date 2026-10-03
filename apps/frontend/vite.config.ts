import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// 127.0.0.1 rather than localhost: Node may resolve localhost to ::1, where
// uvicorn (bound to IPv4 by default) is not listening.
const BACKEND = process.env.SPASHTA_BACKEND_URL ?? 'http://127.0.0.1:8000';

// Everything the browser needs from the backend is same-origin in development:
// the dev server proxies it, so no CORS and the SMART session cookie is first-party.
const proxied = ['/api', '/dicomweb', '/cds-services', '/fhir'];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(proxied.map((path) => [path, { target: BACKEND, changeOrigin: false }])),
  },
  // Cornerstone's DICOM image loader spawns ES-module web workers that load
  // WASM codecs; it must not be pre-bundled, while its CommonJS dependency does.
  optimizeDeps: {
    exclude: ['@cornerstonejs/dicom-image-loader'],
    include: ['dicom-parser'],
  },
  worker: { format: 'es' },
  build: { target: 'es2022', chunkSizeWarningLimit: 4000 },
});
