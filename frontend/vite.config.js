import fs from 'node:fs'
import net from 'node:net'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 端口统一来自 backend/.env 的 PORT（缺省 8080），前端不再写死 8000
function readBackendPort() {
  try {
    const text = fs.readFileSync(new URL('../backend/.env', import.meta.url), 'utf-8')
    const m = text.match(/^\s*PORT\s*=\s*["']?(\d+)/m)
    return m ? Number(m[1]) : 8080
  } catch {
    return 8080
  }
}

const backendPort = readBackendPort()
const backend = `http://127.0.0.1:${backendPort}`

// 检查 127.0.0.1:port 上是否已有服务在监听。
// 不能只依赖 Vite 的“绑定失败才顺延”：Windows 允许 0.0.0.0 与 127.0.0.1 同端口共存，
// 后端占着 8080 时 Vite 仍可能绑定成功，从而抢走用户端入口。
function isPortInUse(port) {
  return new Promise(resolve => {
    const socket = net.connect({ host: '127.0.0.1', port })
    const finish = used => {
      socket.destroy()
      resolve(used)
    }
    socket.setTimeout(600, () => finish(true)) // 超时按占用处理，避免抢端口
    socket.once('connect', () => finish(true))
    socket.once('error', () => finish(false))
  })
}

// dev 端口从 .env 的 PORT 起步，被占用就顺延；找 10 个仍无可用则交给 Vite 报错
async function pickDevPort(start) {
  for (let port = start; port < start + 10; port += 1) {
    if (!(await isPortInUse(port))) return port
  }
  return start
}

export default defineConfig(async () => {
  const devPort = await pickDevPort(backendPort)
  return {
    plugins: [vue()],
    base: '/admin/',
    server: {
      host: '127.0.0.1',
      port: devPort,
      strictPort: true, // 端口已主动选定，绑定失败直接报错，不再静默换端口
      proxy: {
        '/api': { target: backend, changeOrigin: true },
        '/user': { target: backend, changeOrigin: true }
      }
    }
  }
})
