// 统一 API 封装

const TOKEN_KEY = 'gala_token'
export const USER_KEY = 'gala_user'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}
export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

function withAuth(headers = {}) {
  const token = getToken()
  return token ? { ...headers, Authorization: `Bearer ${token}` } : headers
}

const FIELD_LABELS = {
  phone: '手机号',
  password: '密码',
  nickname: '昵称',
  captcha_id: '图形验证码',
  captcha_code: '图形验证码'
}

// 把后端错误（字符串 / Pydantic 校验数组 / 对象）统一转成可读中文
function parseErrorDetail(detail) {
  if (!detail) return ''
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = detail.map(item => {
      if (!item || typeof item !== 'object') return String(item)
      const field = (item.loc || []).filter(p => p !== 'body' && p !== 'query').join('.')
      const label = FIELD_LABELS[field] || field
      let msg = item.msg || ''
      let m = /^String should have at least (\d+) characters?$/.exec(msg)
      if (m) msg = `至少 ${m[1]} 个字符`
      m = /^String should have at most (\d+) characters?$/.exec(msg)
      if (m) msg = `最多 ${m[1]} 个字符`
      if (m || msg === 'Field required') {
        msg = msg === 'Field required' ? '不能为空' : msg
        return label ? `${label}${msg}` : msg
      }
      return label ? `${label}：${msg}` : msg
    }).filter(Boolean)
    return parts.join('；')
  }
  if (typeof detail === 'object') return detail.message || detail.detail || ''
  return ''
}

async function request(url, options = {}) {
  const res = await fetch(url, { ...options, headers: withAuth(options.headers || {}) })
  if (res.status === 401) {
    // 登录态失效：清除本地凭证，由 App 监听事件回到登录页
    clearAuth()
    window.dispatchEvent(new Event('gala:unauthorized'))
    throw new Error('登录状态已失效，请重新登录')
  }
  if (!res.ok) {
    let msg = `请求失败 (${res.status})`
    try { msg = parseErrorDetail((await res.json()).detail) || msg } catch {}
    if (res.status === 403 && msg.includes('管理员')) {
      // 典型场景：同一浏览器在别的标签页换成了普通账号，本页内存仍显示管理员
      msg = '当前登录身份已变化（可能是其他标签页切换了账号），请刷新页面重新登录'
    }
    throw new Error(msg)
  }
  return res.json()
}

function jsonRequest(url, method, data) {
  return request(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
}

function query(params = {}) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== '' && v !== undefined && v !== null) qs.append(k, v)
  })
  const s = qs.toString()
  return s ? `?${s}` : ''
}

export const api = {
  // ---------- 账号与权限 ----------
  authCaptcha: () => request('/api/auth/captcha'),
  // 手机号预检：格式是否合法、是否已被注册
  checkPhone: phone => request(`/api/auth/check-phone?phone=${encodeURIComponent(phone)}`),
  register: payload => jsonRequest('/api/auth/register', 'POST', payload),
  login: payload => jsonRequest('/api/auth/login', 'POST', payload),
  me: () => request('/api/auth/me'),
  logout: () => request('/api/auth/logout', { method: 'POST' }),

  // ---------- 用户管理（仅管理员） ----------
  listUsers: params => request(`/api/users${query(params)}`),
  getUser: id => request(`/api/users/${id}`),
  updateUser: (id, payload) => jsonRequest(`/api/users/${id}`, 'PATCH', payload),
  resetUserPassword: (id, password) =>
    jsonRequest(`/api/users/${id}/reset-password`, 'POST', { password }),
  deleteUser: id => request(`/api/users/${id}`, { method: 'DELETE' }),
  widgetLink: id => request(id ? `/api/users/${id}/widget-link` : '/api/settings/widget-link'),

  // ---------- 对话统计（仅管理员） ----------
  chatStats: days => request(`/api/stats/chat${query({ days })}`),

  // ---------- 文档 ----------
  uploadDocument(file, onProgress, options = {}) {
    const form = new FormData()
    form.append('file', file)
    Object.entries(options).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') form.append(k, v)
    })
    // 用 XHR 以支持上传进度回调
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', '/api/documents/upload')
      const token = getToken()
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
      xhr.upload.onprogress = e => {
        if (e.lengthComputable && onProgress) onProgress(Math.round(e.loaded / e.total * 100))
      }
      xhr.onload = () => {
        try {
          const data = JSON.parse(xhr.responseText)
          xhr.status === 200 ? resolve(data) : reject(new Error(data.detail || '上传失败'))
        } catch { reject(new Error('响应解析失败')) }
      }
      xhr.onerror = () => reject(new Error('网络错误'))
      xhr.send(form)
    })
  },
  listDocuments(params = {}) {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v) qs.append(k, v) })
    const suffix = qs.toString() ? `?${qs}` : ''
    return request(`/api/documents${suffix}`)
  },
  updateDocument(id, payload) {
    return jsonRequest(`/api/documents/${id}`, 'PATCH', payload)
  },
  rebuildDocument(id) {
    return request(`/api/documents/${id}/rebuild`, { method: 'POST' })
  },
  // 上传前预览：该文件实际采用的切分方式与边界依据（后端按分类 + 文件类型计算）
  strategyPreview: (filename, categoryId) =>
    request(`/api/documents/strategy-preview${query({ filename, category_id: categoryId || undefined })}`),
  deleteDocument(id) {
    return request(`/api/documents/${id}`, { method: 'DELETE' })
  },
  documentChunks(id) {
    return request(`/api/documents/${id}/chunks`)
  },
  documentStats() {
    return request('/api/documents/stats')
  },

  // ---------- 分类与知识库 ----------
  listCategories: () => request('/api/categories'),
  createCategory: payload => jsonRequest('/api/categories', 'POST', payload),
  updateCategory: (id, payload) => jsonRequest(`/api/categories/${id}`, 'PATCH', payload),
  deleteCategory: id => request(`/api/categories/${id}`, { method: 'DELETE' }),
  knowledgeOverview: () => request('/api/knowledge/overview'),
  knowledgeFilters: () => request('/api/knowledge/filters'),

  // ---------- 设置 ----------
  getSettings: () => request('/api/settings'),
  updateSettings: payload => jsonRequest('/api/settings', 'PUT', payload),
  rebuildVectors: () => request('/api/settings/rebuild-vectors', { method: 'POST' }),
  importSeed: () => request('/api/settings/seed', { method: 'POST' }),
  // 品牌标识（Logo / 名称），按当前账号
  brand: () => request('/api/settings/brand'),
  uploadBrandLogo(kind, file) {
    const form = new FormData()
    form.append('kind', kind)
    form.append('file', file)
    return request('/api/settings/brand-logo', { method: 'POST', body: form })
  },
  resetBrandLogo: kind => request(`/api/settings/brand-logo?kind=${kind}`, { method: 'DELETE' }),

  // ---------- 问答归档分类 ----------
  qaCategories: () => request('/api/qa-categories'),
  createQaCategory: payload => jsonRequest('/api/qa-categories', 'POST', payload),
  updateQaCategory: (id, payload) => jsonRequest(`/api/qa-categories/${id}`, 'PUT', payload),
  deleteQaCategory: id => request(`/api/qa-categories/${id}`, { method: 'DELETE' }),

  // ---------- 会话 ----------
  listSessions: (source, category) => {
    const params = new URLSearchParams()
    if (source) params.set('source', source)
    if (category) params.set('category', category)
    const qs = params.toString()
    return request(`/api/sessions${qs ? `?${qs}` : ''}`)
  },
  sessionStats: () => request('/api/sessions/stats'),
  deleteSession: id => request(`/api/sessions/${id}`, { method: 'DELETE' }),
  sessionMessages: id => request(`/api/sessions/${id}/messages`),

  // ---------- 问答（SSE 流式，逐事件回调） ----------
  streamChat({ question, sessionId, topK, module, scenario, visibility, onSources, onToken, onSession, onError, onDone }) {
    const ctrl = new AbortController()
    fetch('/api/chat', {
      method: 'POST',
      headers: withAuth({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        question, session_id: sessionId, top_k: topK,
        module: module || '', scenario: scenario || '', visibility: visibility || 'all'
      }),
      signal: ctrl.signal
    })
      .then(async res => {
        if (!res.ok) {
          // 非流式错误：优先展示后端返回的具体原因
          let msg = `请求失败 (${res.status})`
          try { msg = parseErrorDetail((await res.json()).detail) || msg } catch {}
          throw new Error(msg)
        }
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buf = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          const lines = buf.split('\n\n')
          buf = lines.pop() || ''
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            try {
              const event = JSON.parse(line.slice(6))
              if (event.type === 'sources') onSources?.(event.sources)
              else if (event.type === 'token') onToken?.(event.content)
              else if (event.type === 'session') onSession?.(event.session_id)
              else if (event.type === 'error') onError?.(event.message)
            } catch {}
          }
        }
        onDone?.()
      })
      .catch(e => { if (e.name !== 'AbortError') onError?.(e.message) })
    return ctrl // 返回控制器，支持中断
  }
}

// 通用格式化工具
export function formatSize(size) {
  if (!size) return '0 KB'
  return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(size / 1024)} KB`
}

export const TYPE_LABEL = { '.pdf': 'PDF', '.docx': 'Word', '.txt': 'TXT', '.md': 'Markdown', '.pptx': 'PPT' }
export const STATUS_LABEL = { ready: '已就绪', processing: '处理中', failed: '失败' }
