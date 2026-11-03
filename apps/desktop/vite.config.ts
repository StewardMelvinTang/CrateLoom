import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';
export default defineConfig({ root: resolve(__dirname), plugins: [react()], clearScreen: false, server: { port: 1420, strictPort: true }, build: { outDir: 'dist', emptyOutDir: true } });
