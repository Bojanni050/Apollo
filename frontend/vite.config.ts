import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies /api to the FastAPI backend, so the frontend uses
// same-origin relative URLs and no CORS configuration is needed in development.
//
// The ports here are the defaults the startup scripts use: 5273 for this dev
// server, 5274 for the backend. They are deliberately not Vite's 5173 or
// uvicorn's 8000, which are frequently taken by other projects on the same
// machine. Keep in step with scripts\dev.ps1 and backend\app\config.py.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_BACKEND_URL || 'http://127.0.0.1:5274'

  return {
    plugins: [react()],
    server: {
      port: 5273,
      proxy: {
        '/api': { target, changeOrigin: true },
      },
    },
  }
})

