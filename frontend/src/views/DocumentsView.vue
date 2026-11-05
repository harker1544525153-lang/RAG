<script setup>
import { ref, reactive, onMounted } from 'vue'
import { api, formatSize, TYPE_LABEL, STATUS_LABEL } from '../api'

const docs = ref([])
const categories = ref([])
const stats = ref(null)
const filters = reactive({ keyword: '', category_id: '', status: '' })
const uploading = ref(false)
const uploadProgress = ref(0)
const uploadMsg = ref(null)
const dragging = ref(false)
const fileInput = ref(null)
const uploadingCategory = ref('')
const previewDoc = ref(null)
const previewChunks = ref([])
const previewLoading = ref(false)
let searchTimer = null

async function loadDocs() {
  docs.value = await api.listDocuments(filters)
}

async function loadCategories() {
  categories.value = await api.listCategories()
}

async function loadStats() {
  stats.value = await api.documentStats()
}

async function refreshAll() {
  await Promise.all([loadDocs(), loadCategories(), loadStats()])
  window.dispatchEvent(new Event('rag:refresh-health'))
}

function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(loadDocs, 300)
}

function pickFile() { fileInput.value?.click() }

async function handleFiles(files) {
  const file = files?.[0]
  if (!file) return
  uploading.value = true
  uploadProgress.value = 0
  uploadMsg.value = null
  try {
    const res = await api.uploadDocument(
      file,
      p => (uploadProgress.value = p),
      uploadingCategory.value || filters.category_id || undefined
    )
    uploadMsg.value = {
      ok: true,
      text: res.duplicated
        ? `「${res.filename}」已存在，已跳过重复入库`
        : `「${res.filename}」已入库：${res.chunk_count} 个切片，耗时 ${res.elapsed}s`
    }
    await refreshAll()
  } catch (e) {
    uploadMsg.value = { ok: false, text: e.message }
  } finally {
    uploading.value = false
  }
}

function onFileChange(e) {
  handleFiles(e.target.files)
  e.target.value = ''
}

async function removeDoc(doc) {
  if (!confirm(`确认删除「${doc.filename}」？其向量切片将一并清除。`)) return
  await api.deleteDocument(doc.id)
  if (previewDoc.value?.id === doc.id) previewDoc.value = null
  await refreshAll()
}

async function changeCategory(doc, categoryId) {
  if (!categoryId || categoryId === doc.category_id) return
  try {
    await api.updateDocument(doc.id, { category_id: categoryId })
    await refreshAll()
  } catch (e) {
    alert(e.message)
  }
}

async function showChunks(doc) {
  if (previewDoc.value?.id === doc.id) { previewDoc.value = null; return }
  previewDoc.value = doc
  previewLoading.value = true
  previewChunks.value = []
  try { previewChunks.value = await api.documentChunks(doc.id) }
  finally { previewLoading.value = false }
}

onMounted(refreshAll)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">文档管理</div>
        <div class="page-sub">支持 PDF / Word / PPT / TXT / Markdown，上传即解析、分块并入库</div>
      </div>
      <button class="btn-primary" @click="pickFile">＋ 上传文档</button>
    </div>

    <div class="page-body">
      <!-- 统计 -->
      <div class="stat-row" v-if="stats">
        <div class="stat-card">
          <div class="stat-label">文档总数</div>
          <div class="stat-value">{{ stats.total_documents }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">切片总数</div>
          <div class="stat-value">{{ stats.total_chunks }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">存储占用</div>
          <div class="stat-value">{{ formatSize(stats.total_size) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">处理状态</div>
          <div class="stat-status">
            <span class="tag tag-success">{{ stats.status.ready }} 就绪</span>
            <span class="tag tag-processing">{{ stats.status.processing }} 处理中</span>
            <span class="tag tag-danger">{{ stats.status.failed }} 失败</span>
          </div>
        </div>
      </div>

      <!-- 上传区 -->
      <div
        class="upload-card"
        :class="{ dragging }"
        @click="pickFile"
        @dragover.prevent="dragging = true"
        @dragleave="dragging = false"
        @drop.prevent="dragging = false; handleFiles($event.dataTransfer.files)"
      >
        <input ref="fileInput" type="file" hidden accept=".pdf,.docx,.txt,.md,.pptx" @change="onFileChange" />
        <div class="upload-main">
          <div class="upload-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <path d="M7 10 12 5l5 5" />
              <path d="M12 5v12" />
            </svg>
          </div>
          <div>
            <div class="upload-title">点击或拖拽文件到此处上传</div>
            <div class="upload-sub">单文件 ≤ 50MB，上传后自动解析、分块与向量化</div>
          </div>
        </div>
        <div class="upload-side" @click.stop>
          <select v-model="uploadingCategory" class="cat-select">
            <option value="">自动分类</option>
            <option v-for="c in categories" :key="c.id" :value="c.id">{{ c.name }}</option>
          </select>
          <button class="btn-primary" @click="pickFile">选择文件</button>
        </div>
        <div v-if="uploading" class="upload-progress">
          <div class="bar"><div class="bar-inner" :style="{ width: uploadProgress + '%' }"></div></div>
          <span>{{ uploadProgress }}%</span>
        </div>
        <div v-if="uploadMsg" class="upload-msg" :class="uploadMsg.ok ? 'ok' : 'err'">{{ uploadMsg.text }}</div>
      </div>

      <!-- 筛选 -->
      <div class="toolbar">
        <input v-model="filters.keyword" type="text" placeholder="搜索文件名…" class="search-input" @input="onSearch" />
        <select v-model="filters.category_id" @change="loadDocs">
          <option value="">全部分类</option>
          <option v-for="c in categories" :key="c.id" :value="c.id">{{ c.name }}</option>
        </select>
        <select v-model="filters.status" @change="loadDocs">
          <option value="">全部状态</option>
          <option value="ready">已就绪</option>
          <option value="processing">处理中</option>
          <option value="failed">失败</option>
        </select>
        <span class="result-count">共 {{ docs.length }} 条</span>
      </div>

      <!-- 文档列表 -->
      <div class="table-card">
        <table v-if="docs.length">
          <thead>
            <tr>
              <th style="width: 30%">文件名</th>
              <th style="width: 8%">类型</th>
              <th style="width: 9%">大小</th>
              <th style="width: 8%">切片</th>
              <th style="width: 14%">分类</th>
              <th style="width: 9%">状态</th>
              <th style="width: 14%">上传时间</th>
              <th style="width: 12%">操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="doc in docs" :key="doc.id">
              <tr>
                <td class="td-filename" :title="doc.filename">
                  {{ doc.filename }}
                  <span v-if="doc.source === 'seed'" class="seed-chip">初始</span>
                </td>
                <td><span class="type-chip">{{ TYPE_LABEL[doc.file_type] || doc.file_type }}</span></td>
                <td>{{ formatSize(doc.file_size) }}</td>
                <td class="td-chunks">{{ doc.chunk_count }}</td>
                <td>
                  <select
                    class="row-select"
                    :value="doc.category_id || ''"
                    @change="changeCategory(doc, $event.target.value)"
                  >
                    <option value="" disabled>未分类</option>
                    <option v-for="c in categories" :key="c.id" :value="c.id">{{ c.name }}</option>
                  </select>
                </td>
                <td>
                  <span
                    class="tag"
                    :class="doc.status === 'ready' ? 'tag-success' : doc.status === 'failed' ? 'tag-danger' : 'tag-processing'"
                    :title="doc.error || ''"
                  >{{ STATUS_LABEL[doc.status] }}</span>
                </td>
                <td class="td-time">{{ doc.created_at }}</td>
                <td class="td-actions">
                  <button class="btn-ghost" @click="showChunks(doc)">
                    {{ previewDoc?.id === doc.id ? '收起' : '切片' }}
                  </button>
                  <button class="btn-danger-ghost" @click="removeDoc(doc)">删除</button>
                </td>
              </tr>
              <tr v-if="previewDoc?.id === doc.id">
                <td colspan="8" class="chunk-preview-cell">
                  <div v-if="doc.error" class="chunk-error">处理失败：{{ doc.error }}</div>
                  <div v-if="previewLoading" class="chunk-loading">切片加载中...</div>
                  <div v-else-if="!previewChunks.length" class="chunk-loading">暂无切片数据</div>
                  <div v-else class="chunk-list">
                    <div v-for="c in previewChunks" :key="c.id" class="chunk-item">
                      <div class="chunk-meta">
                        #{{ c.metadata.chunk_index }}
                        <span v-if="c.metadata.page" class="chunk-page">第 {{ c.metadata.page }} 页</span>
                        <span class="chunk-len">{{ c.text.length }} 字符</span>
                      </div>
                      <div class="chunk-text">{{ c.text }}</div>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
        <div v-else class="empty">暂无文档，请上传或从 DOC 目录导入</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { height: 100%; display: flex; flex-direction: column; }
.page-body { flex: 1; overflow-y: auto; padding: 16px 24px 24px; display: flex; flex-direction: column; gap: 14px; }

.stat-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.stat-card { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
.stat-label { font-size: 12px; color: var(--text-3); margin-bottom: 6px; }
.stat-value { font-size: 22px; font-weight: 600; color: var(--text); }
.stat-status { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 2px; }

.upload-card {
  position: relative;
  background: var(--panel);
  border: 1.5px dashed #c9d4e8;
  border-radius: var(--radius);
  padding: 16px 20px;
  display: flex; align-items: center; gap: 16px;
  cursor: pointer;
  transition: all 0.2s;
  flex-wrap: wrap;
}
.upload-card:hover, .upload-card.dragging { border-color: var(--primary); background: #f7faff; }
.upload-main { display: flex; align-items: center; gap: 14px; flex: 1; min-width: 240px; }
.upload-icon {
  width: 40px; height: 40px; flex-shrink: 0;
  background: var(--primary-light); color: var(--primary);
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
}
.upload-icon svg { width: 20px; height: 20px; }
.upload-title { font-size: 14px; font-weight: 600; margin-bottom: 3px; }
.upload-sub { color: var(--text-3); font-size: 12px; }
.upload-side { display: flex; align-items: center; gap: 10px; }
.cat-select { min-width: 120px; }
.upload-progress { display: flex; align-items: center; gap: 10px; flex-basis: 100%; max-width: 380px; }
.bar { flex: 1; height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
.bar-inner { height: 100%; background: var(--primary); border-radius: 3px; transition: width 0.2s; }
.upload-msg { flex-basis: 100%; font-size: 12px; }
.upload-msg.ok { color: var(--success); }
.upload-msg.err { color: var(--danger); }

.toolbar { display: flex; align-items: center; gap: 10px; }
.search-input { width: 260px; }
.result-count { margin-left: auto; color: var(--text-3); font-size: 12px; }

.table-card { background: var(--panel); border-radius: var(--radius); border: 1px solid var(--border); overflow: hidden; }
table { width: 100%; border-collapse: collapse; }
th {
  text-align: left; padding: 10px 16px; font-size: 12px; color: var(--text-3); font-weight: 500;
  background: #fafbfd; border-bottom: 1px solid var(--border);
}
td { padding: 11px 16px; border-bottom: 1px solid var(--border); font-size: 13px; vertical-align: middle; }
tbody tr:hover td { background: #fafbfd; }
.td-filename { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.seed-chip {
  display: inline-block; margin-left: 6px; padding: 0 6px;
  font-size: 11px; color: var(--warning); background: #fff7e8; border-radius: 4px;
}
.td-chunks { color: var(--primary); font-weight: 600; }
.td-time { color: var(--text-3); font-size: 12px; }
.td-actions { white-space: nowrap; }
.type-chip { background: #f2f3f5; padding: 2px 8px; border-radius: 4px; font-size: 12px; color: var(--text-2); }
.row-select { width: 100%; padding: 4px 6px; font-size: 12px; border-radius: 6px; }
.empty { padding: 40px; text-align: center; color: var(--text-3); }

.chunk-preview-cell { background: #fafbfd; padding: 12px 16px !important; }
.chunk-loading { color: var(--text-3); font-size: 13px; padding: 8px 0; }
.chunk-error { color: var(--danger); font-size: 13px; padding: 4px 0 10px; }
.chunk-list { max-height: 360px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.chunk-item { background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px; }
.chunk-meta { font-size: 12px; color: var(--primary); font-weight: 600; margin-bottom: 6px; display: flex; gap: 10px; align-items: center; }
.chunk-page, .chunk-len { color: var(--text-3); font-weight: 400; }
.chunk-text { font-size: 12px; color: var(--text-2); line-height: 1.7; white-space: pre-wrap; word-break: break-all; }
</style>
