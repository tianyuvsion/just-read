import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const proxy = { '/api': { target: process.env.JUSTREAD_API_ORIGIN ?? 'http://127.0.0.1:8000', changeOrigin: false } }
export default defineConfig({ plugins: [vue()], server: { proxy }, preview: { proxy } })
