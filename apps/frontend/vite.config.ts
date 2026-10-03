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
  // WASM codecs; it must not be pre-bundled, while its CommonJS dependencies
  // (the parser and the Emscripten codec glue) must be, or the browser gets
  // raw CommonJS with no default export.
  //
  // The metadata package must be pre-bundled too, so the loader shares one
  // instance with @cornerstonejs/core. Loaded raw it becomes a second registry:
  // the loader stores pixel data in one and the viewport reads the other.
  optimizeDeps: {
    exclude: ['@cornerstonejs/dicom-image-loader'],
    include: [
      'dicom-parser',
      '@cornerstonejs/dicom-image-loader > @cornerstonejs/metadata',
      '@cornerstonejs/dicom-image-loader > @cornerstonejs/codec-charls/decodewasmjs',
      '@cornerstonejs/dicom-image-loader > @cornerstonejs/codec-libjpeg-turbo-8bit/decodewasmjs',
      '@cornerstonejs/dicom-image-loader > @cornerstonejs/codec-openjpeg/decodewasmjs',
      '@cornerstonejs/dicom-image-loader > @cornerstonejs/codec-openjph/wasmjs',
    ],
  },
  worker: { format: 'es' },
  build: { target: 'es2022', chunkSizeWarningLimit: 4000 },
});
