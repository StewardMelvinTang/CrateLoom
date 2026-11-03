import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';
export default defineConfig({ root: resolve(__dirname), plugins: [react()], clearScreen: false, server: { port: Number(process.env.CRATELOOM_DEV_PORT || 1420), strictPort: true }, build: { assetsInlineLimit: 0, outDir: 'dist', emptyOutDir: true } });
