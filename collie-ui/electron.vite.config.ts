import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'path'

export default defineConfig({
  main: {
    // Bake the optional account sign-in client config at build time
    // (account-config.ts reads process.env.COLLIE_SUPABASE_*). These are
    // PUBLIC Supabase client values — the anon/publishable key is designed
    // to ship in clients. release.yml sets them on the build step; without
    // them the packaged app reports "sign-in is not configured".
    define: {
      'process.env.COLLIE_SUPABASE_URL': JSON.stringify(
        process.env.COLLIE_SUPABASE_URL ?? ''
      ),
      'process.env.COLLIE_SUPABASE_ANON_KEY': JSON.stringify(
        process.env.COLLIE_SUPABASE_ANON_KEY ?? ''
      )
    },
    build: {
      outDir: 'out/main'
    }
  },
  preload: {
    build: {
      outDir: 'out/preload'
    }
  },
  renderer: {
    root: 'src/renderer',
    build: {
      outDir: 'out/renderer',
      rollupOptions: {
        input: resolve(__dirname, 'src/renderer/index.html')
      }
    },
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src/renderer/src')
      }
    },
    plugins: [react(), tailwindcss()]
  }
})
