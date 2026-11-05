// 统一 API 封装

async function request(url, options = {}) {
  const res = await fetch(url, options)
  if (!res.ok) {
    let msg = `请求失败 (${res.status})`
    try { msg = (await res.json()).detail || msg } catch {}
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

export const api = {
  // ---------- 文档 ----------
  uploadDocument(file, onProgress, categoryId) {
    const form = new FormData()
    form.append('file', file)
    if (categoryId) form.append('category_id', categoryId)
    // 用 XHR 以支持上传进度回调
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', '/api/documents/upload')
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

  // ---------- 设置 ----------
  getSettings: () => request('/api/settings'),
  updateSettings: payload => jsonRequest('/api/settings', 'PUT', payload),
  rebuildVectors: () => request('/api/settings/rebuild-vectors', { method: 'POST' }),
  importSeed: () => request('/api/settings/seed', { method: 'POST' }),

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
  streamChat({ question, sessionId, topK, onSources, onToken, onSession, onError, onDone }) {
    const ctrl = new AbortController()
    fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, session_id: sessionId, top_k: topK }),
      signal: ctrl.signal
    })
      .then(async res => {
        if (!res.ok) throw new Error(`请求失败 (${res.status})`)
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
              else if (event.type === 'done') onDone?.()
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
