<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { api, formatSize, TYPE_LABEL, STATUS_LABEL } from '../api'

const docs = ref([])
const categories = ref([])
const stats = ref(null)
const filters = reactive({ keyword: '', category_id: '', status: '', tier: '' })
const uploading = ref(false)
const uploadProgress = ref(0)
const uploadMsg = ref(null)
const dragging = ref(false)
const fileInput = ref(null)
const previewDoc = ref(null)
const previewChunks = ref([])
const previewLoading = ref(false)
const editDoc = ref(null)
let searchTimer = null

// 上传弹窗：先选文件与元数据，确认后再入库
const uploadOpen = ref(false)
const pendingFile = ref(null)
const uploadError = ref(null)
// 上传前由后端计算的实际切分方式与边界依据（分类为空 = 默认配置）
const strategyPreview = ref(null)
// 分级权重（读取运行时设置，失败时使用默认值展示）
const tierWeights = reactive({ core: 1.15, standard: 1.0, archived: 0.7 })

// 上传时的结构化元数据：分级 / 业务模块 / 适用场景 / 权限 / 有效期
const uploadMeta = reactive({
  category_id: '', tier: 'standard', importance: 5,
  module: '', scenario: '', visibility: 'public',
  effective_date: '', expire_date: '',
})
const editForm = reactive({ category_id: '', tier: 'standard', importance: 5, module: '', scenario: '',
  visibility: 'public', effective_date: '', expire_date: '' })
// 设置面板中当前所选分类（用于展示分块策略说明）
const editCategory = computed(
  () => categories.value.find(c => c.id === editForm.category_id) || null
)
// 设置弹窗：当前所选分类实际采用的切分方式（与上传弹窗同一接口）
const editStrategyPreview = ref(null)

async function refreshEditStrategyPreview() {
  const doc = editDoc.value
  if (!doc) { editStrategyPreview.value = null; return }
  const key = `${doc.id}|${editForm.category_id}`
  try {
    const data = await api.strategyPreview(
      doc.filename,
      editForm.category_id === 'auto' ? '' : editForm.category_id
    )
    // 避免快速切换分类时旧请求覆盖新结果
    if (key === `${editDoc.value?.id || ''}|${editForm.category_id}`) {
      editStrategyPreview.value = data
    }
  } catch { editStrategyPreview.value = null }
}

const TIER_LABEL = { core: '核心权威', standard: '普通参考', archived: '过期归档' }
const VISIBILITY_LABEL = { public: '公开', internal: '内部', admin: '管理端' }
const TIER_OPTIONS = [
  { value: 'core', label: '核心权威' },
  { value: 'standard', label: '普通参考' },
  { value: 'archived', label: '过期归档' },
]

// 分块策略名称、边界依据与块大小均由后端分类接口下发（strategy_label / strategy_boundary / size_unit）
const TIER_DESC = {
  core: '最新制度、官方手册等权威资料，检索时优先命中。',
  standard: '日常参考文档，正常参与排序。',
  archived: '历史归档，权重最低；已过有效期时自动剔除（核心权威不受过期过滤影响）。',
}

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

// 打开上传弹窗：可带入拖拽/选择的文件
function openUploadDialog(files) {
  const file = files?.[0]
  if (file) pendingFile.value = file
  uploadError.value = null
  uploadMsg.value = null
  // 每次上传默认选中「默认配置」（引用设置参数并按内容自动识别分类）
  uploadMeta.category_id = ''
  uploadOpen.value = true
  if (!categories.value.length) loadCategories()
  loadTierWeights()
}

function closeUploadDialog() {
  if (uploading.value) return
  uploadOpen.value = false
  pendingFile.value = null
  uploadError.value = null
  strategyPreview.value = null
}

// 文件或分类变化时，向后端确认实际采用的切分方式与边界依据
async function refreshStrategyPreview() {
  const file = pendingFile.value
  if (!file) { strategyPreview.value = null; return }
  const key = `${file.name}|${uploadMeta.category_id}`
  try {
    const data = await api.strategyPreview(file.name, uploadMeta.category_id)
    // 避免快速切换时旧请求覆盖新结果
    if (key === `${pendingFile.value?.name || ''}|${uploadMeta.category_id}`) {
      strategyPreview.value = data
    }
  } catch { strategyPreview.value = null }
}

watch([pendingFile, () => uploadMeta.category_id], refreshStrategyPreview)
watch([() => editDoc.value?.id, () => editForm.category_id], refreshEditStrategyPreview)

async function loadTierWeights() {
  try {
    const s = await api.getSettings()
    const valueOf = key => {
      const item = (s.editable || []).find(x => x.key === key)
      return item ? Number(item.value) : null
    }
    const core = valueOf('tier_core_weight')
    const standard = valueOf('tier_standard_weight')
    const archived = valueOf('tier_archived_weight')
    if (core != null) tierWeights.core = core
    if (standard != null) tierWeights.standard = standard
    if (archived != null) tierWeights.archived = archived
  } catch { /* 读取失败时保留默认展示值 */ }
}

async function confirmUpload() {
  if (!pendingFile.value) { pickFile(); return }
  await handleFiles([pendingFile.value])
}

async function handleFiles(files) {
  const file = files?.[0]
  if (!file) return
  uploading.value = true
  uploadProgress.value = 0
  uploadError.value = null
  try {
    const res = await api.uploadDocument(
      file,
      p => (uploadProgress.value = p),
      { ...uploadMeta }
    )
    const strategyLabel = (categories.value.find(c => c.id === uploadMeta.category_id) || {}).strategy_label
    const detail = res.auto_category
      ? `默认配置 · 按内容识别为「${res.category_name}」`
      : (strategyLabel || '')
    uploadMsg.value = {
      ok: true,
      text: res.duplicated
        ? `「${res.filename}」已存在，已跳过重复入库`
        : `「${res.filename}」已入库：${res.chunk_count} 个切片${detail ? `（${detail}）` : ''}，耗时 ${res.elapsed}s`
    }
    uploadOpen.value = false
    pendingFile.value = null
    await refreshAll()
  } catch (e) {
    uploadError.value = e.message
  } finally {
    uploading.value = false
  }
}

function onFileChange(e) {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (file) openUploadDialog([file])
}

function onUploadDrop(e) {
  dragging.value = false
  const file = e.dataTransfer.files?.[0]
  if (file) openUploadDialog([file])
}

async function removeDoc(doc) {
  if (!confirm(`确认删除「${doc.filename}」？其向量切片将一并清除。`)) return
  await api.deleteDocument(doc.id)
  if (previewDoc.value?.id === doc.id) previewDoc.value = null
  await refreshAll()
}

async function changeCategory(doc, categoryId) {
  const current = doc.auto_category ? 'auto' : (doc.category_id || '')
  if (!categoryId || categoryId === current) return
  try {
    await api.updateDocument(doc.id, categoryId === 'auto' ? { auto_category: true } : { category_id: categoryId })
    await refreshAll()
  } catch (e) {
    alert(e.message)
  }
}

function openEdit(doc) {
  editDoc.value = doc
  Object.assign(editForm, {
    category_id: doc.auto_category ? 'auto' : (doc.category_id || 'auto'),
    tier: doc.tier || 'standard',
    importance: doc.importance || 5,
    module: doc.module || '',
    scenario: doc.scenario || '',
    visibility: doc.visibility || 'public',
    effective_date: doc.effective_date || '',
    expire_date: doc.expire_date || '',
  })
  refreshEditStrategyPreview()
}

async function saveMeta(doc) {
  try {
    const payload = { ...editForm }
    if (editForm.category_id === 'auto') {
      // 「默认配置」：不指定分类，由后端按内容自动识别
      payload.auto_category = true
      delete payload.category_id
    } else {
      payload.auto_category = false
    }
    const res = await api.updateDocument(doc.id, payload)
    editDoc.value = null
    if (res._warning) alert(res._warning)
    await refreshAll()
  } catch (e) {
    alert(e.message)
  }
}

async function rebuildDoc(doc) {
  if (!confirm(`按「${doc.category_name || '未分类'}」的当前策略重建「${doc.filename}」切片？`)) return
  try {
    await api.rebuildDocument(doc.id)
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
        <div class="page-sub">不同文档类型自动采用不同切分：PDF / PPT 按页码、Word / TXT 按段落标题、Markdown 与制度按语义、代码按函数结构、表格按行组；上传即解析、分块、向量化并写入分级标签</div>
      </div>
      <button class="btn-primary" @click="openUploadDialog()">＋ 上传文档</button>
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

      <!-- 上传入口：点击或拖拽后先弹出元数据配置，确认后才入库 -->
      <div
        class="upload-card"
        :class="{ dragging }"
        @click="openUploadDialog()"
        @dragover.prevent="dragging = true"
        @dragleave="dragging = false"
        @drop.prevent="onUploadDrop"
      >
        <input ref="fileInput" type="file" hidden
               accept=".pdf,.docx,.txt,.md,.markdown,.pptx,.py,.js,.ts,.jsx,.tsx,.java,.go,.sql,.sh,.ps1,.json,.yaml,.yml,.html,.css,.vue"
               @change="onFileChange" />
        <div class="upload-main">
          <div class="upload-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <path d="M7 10 12 5l5 5" />
              <path d="M12 5v12" />
            </svg>
          </div>
          <div>
            <div class="upload-title">上传前先设置分类、分级与标注</div>
            <div class="upload-sub">
              单文件 ≤ 50MB；PDF / Word / PPT / TXT / Markdown / 代码文件。系统按文档类型选择切分方式（PDF/PPT 按页、Word 按段落标题、代码按函数、表格按行组），分级与标注决定检索排序与过滤
            </div>
          </div>
        </div>
        <div class="upload-side" @click.stop>
          <button class="btn-primary" @click="openUploadDialog()">选择文件并配置</button>
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
        <select v-model="filters.tier" @change="loadDocs">
          <option value="">全部分级</option>
          <option value="core">核心权威</option>
          <option value="standard">普通参考</option>
          <option value="archived">过期归档</option>
        </select>
        <span class="result-count">共 {{ docs.length }} 条</span>
      </div>

      <!-- 文档列表 -->
      <div class="table-card">
        <table v-if="docs.length">
          <thead>
            <tr>
              <th style="width: 18%">文件名</th>
              <th style="width: 5%">类型</th>
              <th style="width: 5%">切片</th>
              <th style="width: 9%">分类</th>
              <th style="width: 11%">分块策略</th>
              <th style="width: 6%">综合得分</th>
              <th style="width: 7%">分级</th>
              <th style="width: 8%">生效日期</th>
              <th style="width: 8%">过期日期</th>
              <th style="width: 7%">有效期状态</th>
              <th style="width: 7%">更新</th>
              <th style="width: 9%">操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="doc in docs" :key="doc.id">
              <tr :class="{ 'row-expired': doc.effective_status === 'expired' }">
                <td class="td-filename" :title="`${doc.filename} · ${formatSize(doc.file_size)}`">
                  {{ doc.filename }}
                  <span v-if="doc.source === 'seed'" class="seed-chip">初始</span>
                </td>
                <td><span class="type-chip">{{ TYPE_LABEL[doc.file_type] || doc.file_type }}</span></td>
                <td class="td-chunks">{{ doc.chunk_count }}</td>
                <td>
                  <select
                    class="row-select"
                    :value="doc.auto_category ? 'auto' : (doc.category_id || '')"
                    @change="changeCategory(doc, $event.target.value)"
                  >
                    <option value="auto">默认（自动识别）</option>
                    <option v-for="c in categories" :key="c.id" :value="c.id">{{ c.name }}</option>
                  </select>
                  <span v-if="doc.auto_category" class="auto-chip" style="margin-top:4px">自动</span>
                </td>
                <td>
                  <span class="strategy-chip">{{ doc.strategy_label }}</span>
                  <div class="strategy-hint">
                    {{ doc.chunk_size || '—' }} 字符 · 重叠 {{ doc.chunk_overlap ?? '—' }}
                  </div>
                </td>
                <td class="td-score">
                  <span class="score-bar" :style="{ width: Math.min(100, (doc.base_score || 0) * 80) + '%' }"></span>
                  <span class="score-text">{{ doc.base_score ?? '—' }}</span>
                </td>
                <td>
                  <span class="tier-chip" :class="'tier-' + doc.tier">{{ TIER_LABEL[doc.tier] || doc.tier }}</span>
                </td>
                <td class="td-date">{{ doc.effective_date || '—' }}</td>
                <td class="td-date">{{ doc.expire_date || '长期' }}</td>
                <td>
                  <span
                    class="tag"
                    :class="doc.effective_status === 'expired' ? 'tag-danger' : 'tag-success'"
                  >{{ doc.effective_status === 'expired' ? '已过期' : '未过期' }}</span>
                </td>
                <td class="td-time">{{ doc.updated_at }}</td>
                <td class="td-actions">
                  <button class="btn-ghost" @click="showChunks(doc)">
                    {{ previewDoc?.id === doc.id ? '收起' : '切片' }}
                  </button>
                  <button class="btn-ghost" @click="openEdit(doc)">设置</button>
                  <button class="btn-danger-ghost" @click="removeDoc(doc)">删除</button>
                </td>
              </tr>
              <!-- 文档设置在「设置」弹窗中进行（见下方 dialog） -->
              <tr v-if="previewDoc?.id === doc.id">
                <td colspan="12" class="chunk-preview-cell">
                  <div v-if="doc.error" class="chunk-error">处理失败：{{ doc.error }}</div>
                  <div v-if="previewLoading" class="chunk-loading">切片加载中...</div>
                  <div v-else-if="!previewChunks.length" class="chunk-loading">暂无切片数据</div>
                  <div v-else class="chunk-list">
                    <div v-for="c in previewChunks" :key="c.id" class="chunk-item">
                      <div class="chunk-meta">
                        #{{ c.metadata.chunk_index }}
                        <span v-if="c.metadata.page" class="chunk-page">第 {{ c.metadata.page }} 页</span>
                        <span v-if="c.metadata.section" class="chunk-page">章节：{{ c.metadata.section }}</span>
                        <span v-if="c.metadata.chunk_kind" class="chunk-page">{{ c.metadata.chunk_kind }}</span>
                        <span class="chunk-len">{{ c.text.length }} 字符 · 约 {{ c.metadata.tokens || 0 }} Token</span>
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

    <!-- 上传配置弹窗：先选分类 / 分级 / 标注，确认后再上传 -->
    <div v-if="uploadOpen" class="dialog-mask" @click.self="closeUploadDialog">
      <div class="upload-dialog">
        <div class="dialog-head">
          <div>
            <h3>上传文档 · 先设置元数据</h3>
            <p>分类决定分块方式；分级与标注决定检索时的排序与过滤。上传后仍可在列表「设置」中修改。</p>
          </div>
          <button class="dialog-close" :disabled="uploading" @click="closeUploadDialog">×</button>
        </div>

        <div class="dialog-body">
          <!-- 文件 -->
          <section class="dlg-section">
            <div class="dlg-title">文件</div>
            <div class="file-pick-row">
              <button class="btn-ghost" :disabled="uploading" @click="pickFile">选择文件</button>
              <span v-if="pendingFile" class="file-name">
                {{ pendingFile.name }} · {{ formatSize(pendingFile.size) }}
              </span>
              <span v-else class="file-empty">尚未选择文件；也可直接把文件拖到上传区，会自动带入这里</span>
            </div>
          </section>

          <!-- ① 文档分类 -->
          <section class="dlg-section">
            <div class="dlg-title">① 文档分类 <span class="dlg-sub">决定分块策略</span></div>
            <div v-if="strategyPreview" class="strategy-preview">
              <div class="sp-main">
                <span class="sp-tag">实际切分</span>
                <strong>{{ strategyPreview.strategy_label }}</strong>
                <span class="sp-unit">
                  目标块 ≤ {{ strategyPreview.chunk_size }} {{ strategyPreview.size_unit }} ·
                  重叠 {{ strategyPreview.chunk_overlap }} {{ strategyPreview.size_unit }}
                </span>
              </div>
              <div class="sp-boundary">边界依据：{{ strategyPreview.strategy_boundary }}</div>
              <div class="sp-note">{{ strategyPreview.note }}</div>
            </div>
            <div v-else class="strategy-preview empty">
              选择文件后，这里展示后端实际采用的切分方式与边界依据
            </div>
            <div class="cat-grid">
              <label class="cat-opt" :class="{ active: uploadMeta.category_id === '' }">
                <input type="radio" v-model="uploadMeta.category_id" value="" />
                <div class="cat-opt-head">
                  <span class="cat-opt-name">默认配置</span>
                  <span class="cat-opt-tag">推荐 · 上传默认</span>
                </div>
                <div class="cat-opt-size">识别后按该文档类型的目标块大小与重叠切分</div>
                <div class="cat-opt-desc">
                  不手动指定分类：后端按文档实际内容识别类型（代码、表格、流程、制度、长文档各有独立判定标准），
                  再按文件类型兜底切分方式；参数取自「设置 → 按文档类型的分块参数」，下方各分类卡片即当前生效值。
                </div>
                <div class="cat-opt-count">自动识别 · 无需选择</div>
              </label>
              <label
                v-for="c in categories"
                :key="c.id"
                class="cat-opt"
                :class="{ active: uploadMeta.category_id === c.id }"
              >
                <input type="radio" v-model="uploadMeta.category_id" :value="c.id" />
                <div class="cat-opt-head">
                  <span class="cat-opt-name">{{ c.name }}</span>
                  <span class="cat-opt-tag">{{ c.strategy_label }}</span>
                </div>
                <div class="cat-opt-size">
                  目标块 ≤ {{ c.chunk_size }} {{ c.size_unit }} · 重叠 {{ c.chunk_overlap }} {{ c.size_unit }}
                </div>
                <div class="cat-opt-desc">边界依据：{{ c.strategy_boundary }}</div>
                <div class="cat-opt-desc note">达到目标大小即切分；遇到边界会提前断开；超长内容二次切分时保留重叠。</div>
                <div class="cat-opt-count">
                  {{ c.doc_count || 0 }} 篇文档
                  <span class="cat-strategy-key">{{ c.strategy }}</span>
                </div>
              </label>
            </div>
            <div class="dlg-foot-note">
              块大小是「目标上限」而非硬性最大值：累计达到目标即切分，遇到标题 / 条款 / 步骤 / 页码等边界会提前断开；
              超长内容二次切分时，相邻块保留设定的重叠上下文（768 字符 ≈ 512 Token）。
            </div>
          </section>

          <!-- ② 数据分级与权重 -->
          <section class="dlg-section">
            <div class="dlg-title">② 数据分级与权重 <span class="dlg-sub">决定检索排序</span></div>
            <div class="formula-bar">
              综合得分 = <b>相似度</b> × <b>分级权重</b> × <b>时间衰减</b> × <b>重要度系数</b>
              <div class="formula-sub">
                时间衰减 = 0.5^(文档年龄 / 半衰期)，并设有下限；重要度系数 = 0.5 + 0.1 × 重要度。
                权重和半衰期可在「设置 → 检索、分级与时间权重」调整。
              </div>
            </div>
            <div class="tier-grid">
              <label
                v-for="t in TIER_OPTIONS"
                :key="t.value"
                class="tier-opt"
                :class="['tier-' + t.value, { active: uploadMeta.tier === t.value }]"
              >
                <input type="radio" v-model="uploadMeta.tier" :value="t.value" />
                <div class="tier-opt-head">
                  <span class="tier-opt-name">{{ t.label }}</span>
                  <span class="tier-weight">×{{ tierWeights[t.value] }}</span>
                </div>
                <div class="tier-opt-desc">{{ TIER_DESC[t.value] }}</div>
              </label>
            </div>
            <div class="importance-row">
              <label class="importance-label">重要度 <span class="imp-value">{{ uploadMeta.importance }}</span></label>
              <input v-model.number="uploadMeta.importance" type="range" min="1" max="10" step="1" class="imp-range" />
              <span class="dlg-hint">1~10，越大排序越靠前（系数范围 0.6~1.5）</span>
            </div>
          </section>

          <!-- ③ 结构化标注 -->
          <section class="dlg-section">
            <div class="dlg-title">③ 结构化标注 <span class="dlg-sub">用于检索过滤，从源头减少噪音</span></div>
            <div class="meta-grid">
              <div class="meta-item">
                <label>业务模块</label>
                <input v-model="uploadMeta.module" type="text" placeholder="如 会员 / 订单 / 售后" />
                <div class="dlg-hint">问答页可按模块限定检索范围，只命中该模块的资料。</div>
              </div>
              <div class="meta-item">
                <label>适用场景</label>
                <input v-model="uploadMeta.scenario" type="text" placeholder="如 积分规则 / 退款流程" />
                <div class="dlg-hint">更细粒度的使用场景，与模块组合过滤，减少跨场景噪音。</div>
              </div>
              <div class="meta-item">
                <label>权限范围</label>
                <select v-model="uploadMeta.visibility">
                  <option value="public">公开（用户端 + 管理端）</option>
                  <option value="internal">内部（管理端与内部检索）</option>
                  <option value="admin">管理端（仅后台检索）</option>
                </select>
                <div class="dlg-hint">用户端问答强制只检索「公开」文档，避免内部资料外泄。</div>
              </div>
              <div class="meta-item">
                <label>生效日期</label>
                <input v-model="uploadMeta.effective_date" type="date" />
                <div class="dlg-hint">留空则按上传时间计算；新文档的时间权重高于旧文档。</div>
              </div>
              <div class="meta-item">
                <label>过期日期</label>
                <input v-model="uploadMeta.expire_date" type="date" />
                <div class="dlg-hint">过期后自动降权并在检索中剔除（核心权威不受影响）；留空表示长期有效。</div>
              </div>
              <div class="meta-item">
                <label>文档类型 / 更新时间</label>
                <div class="meta-readonly">
                  类型：{{ (categories.find(c => c.id === uploadMeta.category_id) || {}).name || '自动匹配分类' }}
                  <br />更新时间：入库时自动记录
                </div>
                <div class="dlg-hint">文档类型用于来源卡片展示；更新时间是时间权重的计算基准。</div>
              </div>
            </div>
          </section>
        </div>

        <div class="dialog-foot">
          <div v-if="uploading" class="upload-progress">
            <div class="bar"><div class="bar-inner" :style="{ width: uploadProgress + '%' }"></div></div>
            <span>{{ uploadProgress }}%</span>
          </div>
          <div v-else-if="uploadError" class="upload-msg err">{{ uploadError }}</div>
          <div v-else class="foot-hint">确认后将按上方「实际切分」的策略与边界分块、向量化，并写入分级标签。</div>
          <div class="foot-actions">
            <button class="btn-ghost" :disabled="uploading" @click="closeUploadDialog">取消</button>
            <button class="btn-primary" :disabled="uploading" @click="confirmUpload">
              {{ uploading ? '上传中…' : (pendingFile ? '开始上传' : '选择文件') }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 文档设置弹窗：与上传时相同的分类 / 分级 / 标注，保存后按新配置重建切片 -->
    <div v-if="editDoc" class="dialog-mask" @click.self="editDoc = null">
      <div class="upload-dialog">
        <div class="dialog-head">
          <div>
            <h3>文档设置 · {{ editDoc.filename }}</h3>
            <p>分类决定分块方式；分级与标注决定检索排序与过滤。保存后自动重建切片，修改立即生效。</p>
          </div>
          <button class="dialog-close" @click="editDoc = null">×</button>
        </div>

        <div class="dialog-body">
          <!-- ① 文档分类：与上传弹窗同一套卡片与切分信息 -->
          <section class="dlg-section">
            <div class="dlg-title">① 文档分类 <span class="dlg-sub">决定分块策略</span></div>
            <div v-if="editStrategyPreview" class="strategy-preview">
              <div class="sp-main">
                <span class="sp-tag">实际切分</span>
                <strong>{{ editStrategyPreview.strategy_label }}</strong>
                <span class="sp-unit">
                  目标块 ≤ {{ editStrategyPreview.chunk_size }} {{ editStrategyPreview.size_unit }} ·
                  重叠 {{ editStrategyPreview.chunk_overlap }} {{ editStrategyPreview.size_unit }}
                </span>
              </div>
              <div class="sp-boundary">边界依据：{{ editStrategyPreview.strategy_boundary }}</div>
              <div class="sp-note">{{ editStrategyPreview.note }}</div>
            </div>
            <div v-else class="strategy-preview empty">正在读取该文档实际采用的切分方式与边界依据…</div>
            <div class="cat-grid">
              <label class="cat-opt" :class="{ active: editForm.category_id === 'auto' }">
                <input type="radio" v-model="editForm.category_id" value="auto" />
                <div class="cat-opt-head">
                  <span class="cat-opt-name">默认配置</span>
                  <span class="cat-opt-tag">按内容识别</span>
                </div>
                <div class="cat-opt-size">识别后按该文档类型的目标块大小与重叠切分</div>
                <div class="cat-opt-desc">
                  不手动指定分类：按文档实际内容重新识别类型（代码、表格、流程、制度、长文档各有独立判定标准），
                  再按文件类型兜底切分方式；参数取自「设置 → 按文档类型的分块参数」。
                </div>
                <div class="cat-opt-count">
                  <span v-if="editDoc.auto_category" class="auto-chip">当前为自动识别</span>
                  <span v-else>保存后转为自动识别</span>
                </div>
              </label>
              <label
                v-for="c in categories"
                :key="'e' + c.id"
                class="cat-opt"
                :class="{ active: editForm.category_id === c.id }"
              >
                <input type="radio" v-model="editForm.category_id" :value="c.id" />
                <div class="cat-opt-head">
                  <span class="cat-opt-name">{{ c.name }}</span>
                  <span class="cat-opt-tag">{{ c.strategy_label }}</span>
                </div>
                <div class="cat-opt-size">
                  目标块 ≤ {{ c.chunk_size }} {{ c.size_unit }} · 重叠 {{ c.chunk_overlap }} {{ c.size_unit }}
                </div>
                <div class="cat-opt-desc">边界依据：{{ c.strategy_boundary }}</div>
                <div class="cat-opt-desc note">达到目标大小即切分；遇到边界会提前断开；超长内容二次切分时保留重叠。</div>
                <div class="cat-opt-count">
                  {{ c.doc_count || 0 }} 篇文档
                  <span class="cat-strategy-key">{{ c.strategy }}</span>
                </div>
              </label>
            </div>
            <div class="dlg-foot-note">
              块大小是「目标上限」而非硬性最大值：累计达到目标即切分，遇到标题 / 条款 / 步骤 / 页码等边界会提前断开；
              超长内容二次切分时，相邻块保留设定的重叠上下文（768 字符 ≈ 512 Token）。
            </div>
          </section>

          <section class="dlg-section">
            <div class="dlg-title">② 数据分级与权重 <span class="dlg-sub">决定检索排序</span></div>
            <div class="formula-bar">
              综合得分 = <b>相似度</b> × <b>分级权重</b> × <b>时间衰减</b> × <b>重要度系数</b>
              <div class="formula-sub">
                时间衰减 = 0.5^(文档年龄 / 半衰期)，并设有下限；重要度系数 = 0.5 + 0.1 × 重要度。
                权重和半衰期可在「设置 → 检索、分级与时间权重」调整。
              </div>
            </div>
            <div class="tier-grid">
              <label
                v-for="t in TIER_OPTIONS"
                :key="'e' + t.value"
                class="tier-opt"
                :class="['tier-' + t.value, { active: editForm.tier === t.value }]"
              >
                <input type="radio" v-model="editForm.tier" :value="t.value" />
                <div class="tier-opt-head">
                  <span class="tier-opt-name">{{ t.label }}</span>
                  <span class="tier-weight">×{{ tierWeights[t.value] }}</span>
                </div>
                <div class="tier-opt-desc">{{ TIER_DESC[t.value] }}</div>
              </label>
            </div>
            <div class="importance-row">
              <label class="importance-label">重要度 <span class="imp-value">{{ editForm.importance }}</span></label>
              <input v-model.number="editForm.importance" type="range" min="1" max="10" step="1" class="imp-range" />
              <span class="dlg-hint">1~10，越大排序越靠前（系数范围 0.6~1.5）</span>
            </div>
          </section>

          <section class="dlg-section">
            <div class="dlg-title">③ 结构化标注 <span class="dlg-sub">检索时按模块 / 场景 / 权限过滤，从源头减少噪音</span></div>
            <div class="meta-grid">
              <div class="meta-item">
                <label>业务模块</label>
                <input v-model="editForm.module" type="text" placeholder="如 会员 / 订单 / 售后" />
                <div class="dlg-hint">问答页可按模块限定检索范围。</div>
              </div>
              <div class="meta-item">
                <label>适用场景</label>
                <input v-model="editForm.scenario" type="text" placeholder="如 积分规则 / 退款流程" />
                <div class="dlg-hint">与模块组合过滤，减少跨场景噪音。</div>
              </div>
              <div class="meta-item">
                <label>权限范围</label>
                <select v-model="editForm.visibility">
                  <option value="public">公开（用户端 + 管理端）</option>
                  <option value="internal">内部（管理端与内部检索）</option>
                  <option value="admin">管理端（仅后台检索）</option>
                </select>
                <div class="dlg-hint">用户端问答强制只检索「公开」文档。</div>
              </div>
              <div class="meta-item">
                <label>生效日期</label>
                <input v-model="editForm.effective_date" type="date" />
                <div class="dlg-hint">留空按上传时间计算；越新时间权重越高。</div>
              </div>
              <div class="meta-item">
                <label>过期日期</label>
                <input v-model="editForm.expire_date" type="date" />
                <div class="dlg-hint">过期后降权并剔除（核心权威除外）。</div>
              </div>
              <div class="meta-item">
                <label>文档类型 / 更新时间</label>
                <div class="meta-readonly">
                  类型：{{ editCategory ? editCategory.name : (editDoc.category_name || '未分类') }}
                  <span v-if="editDoc.auto_category" class="auto-chip">自动识别</span>
                  <br />更新时间：{{ editDoc.updated_at || editDoc.created_at || '—' }}
                </div>
                <div class="dlg-hint">类型用于来源卡片展示；更新时间是时间权重的基准。</div>
              </div>
            </div>
          </section>
        </div>

        <div class="dialog-foot">
          <div class="foot-hint">保存后会自动按当前配置重新切片并向量化。</div>
          <div class="foot-actions">
            <button class="btn-ghost" @click="rebuildDoc(editDoc)">仅重建切片</button>
            <button class="btn-ghost" @click="editDoc = null">取消</button>
            <button class="btn-primary" @click="saveMeta(editDoc)">保存并重建</button>
          </div>
        </div>
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
.upload-progress { display: flex; align-items: center; gap: 10px; flex-basis: 100%; max-width: 380px; }
.bar { flex: 1; height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
.bar-inner { height: 100%; background: var(--primary); border-radius: 3px; transition: width 0.2s; }
.upload-msg { flex-basis: 100%; font-size: 12px; }
.upload-msg.ok { color: var(--success); }
.upload-msg.err { color: var(--danger); }
.imp-range { width: 120px; }
.imp-value { color: var(--primary); font-weight: 600; min-width: 16px; }

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
.strategy-hint { margin-top: 3px; font-size: 11px; color: var(--text-3); }
.strategy-chip {
  display: inline-block; padding: 2px 8px; border-radius: 4px;
  background: #e8f3ff; color: #1a64c5; font-size: 12px;
}

/* 综合得分 */
.td-score { position: relative; min-width: 60px; }
.score-bar {
  position: absolute; left: 2px; top: 50%; transform: translateY(-50%);
  height: 18px; background: linear-gradient(90deg, #e8f3ff, #c2e7ff);
  border-radius: 3px; z-index: 0; opacity: 0.7;
}
.score-text {
  position: relative; z-index: 1; font-size: 12px; font-weight: 600; color: #1a64c5;
}

/* 日期列 */
.td-date { font-size: 12px; color: var(--text-2); white-space: nowrap; }

/* 已过期行 */
.row-expired td { color: var(--text-3); background: #fafafa; }
.row-expired td.td-filename { text-decoration: line-through; }
.auto-chip {
  display: inline-block; padding: 0 5px; margin-right: 3px; border-radius: 3px;
  background: #e8f7ef; color: #0bae6e; font-size: 10px;
}
.tier-chip {
  display: inline-block; padding: 1px 8px; border-radius: 4px;
  font-size: 11px; font-weight: 500; white-space: nowrap;
}
.tier-chip.tier-core { background: #fff1e8; color: #d25f00; }
.tier-chip.tier-standard { background: #e8f7ee; color: #12833b; }
.tier-chip.tier-archived { background: #f2f3f5; color: #86909c; }
.td-tags { font-size: 12px; }
.meta-chip {
  display: inline-block; margin: 0 4px 3px 0; padding: 1px 6px;
  background: #f2f5fa; color: var(--text-2); border-radius: 4px; font-size: 11px;
}
.tags-date { font-size: 11px; color: var(--text-3); margin-top: 2px; }
.empty { padding: 40px; text-align: center; color: var(--text-3); }

.chunk-preview-cell { background: #fafbfd; padding: 12px 16px !important; }
.chunk-loading { color: var(--text-3); font-size: 13px; padding: 8px 0; }
.chunk-error { color: var(--danger); font-size: 13px; padding: 4px 0 10px; }
.chunk-list { max-height: 360px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.chunk-item { background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px; }
.chunk-meta { font-size: 12px; color: var(--primary); font-weight: 600; margin-bottom: 6px; display: flex; gap: 10px; align-items: center; }
.chunk-page, .chunk-len { color: var(--text-3); font-weight: 400; }
.chunk-text { font-size: 12px; color: var(--text-2); line-height: 1.7; white-space: pre-wrap; word-break: break-all; }

/* ---------- 上传配置弹窗 ---------- */
.dialog-mask {
  position: fixed; inset: 0; z-index: 60;
  background: rgba(15, 23, 42, 0.45);
  display: flex; align-items: center; justify-content: center;
  padding: 20px;
}
.upload-dialog {
  width: min(960px, 100%);
  max-height: calc(100vh - 40px);
  background: var(--panel);
  border-radius: 12px;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.22);
  display: flex; flex-direction: column;
  overflow: hidden;
}
.dialog-head {
  display: flex; align-items: flex-start; gap: 16px;
  padding: 16px 20px 14px;
  border-bottom: 1px solid var(--border);
}
.dialog-head h3 { margin: 0 0 4px; font-size: 16px; color: var(--text); }
.dialog-head p { margin: 0; font-size: 12px; color: var(--text-3); line-height: 1.6; }
.dialog-close {
  margin-left: auto; width: 28px; height: 28px; flex-shrink: 0;
  border: none; border-radius: 6px; background: #f2f4f8; color: var(--text-3);
  font-size: 18px; line-height: 1; cursor: pointer;
}
.dialog-close:hover { background: #e8ecf4; color: var(--text); }
.dialog-body { flex: 1; overflow-y: auto; padding: 16px 20px 6px; display: flex; flex-direction: column; gap: 16px; }
.dlg-section { display: flex; flex-direction: column; gap: 10px; }
.dlg-title { font-size: 13px; font-weight: 600; color: var(--text); display: flex; align-items: baseline; gap: 8px; }
.dlg-sub { font-size: 11px; font-weight: 400; color: var(--text-3); }
.dlg-hint { font-size: 11px; color: var(--text-3); line-height: 1.5; }

.file-pick-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.file-name { font-size: 12px; color: var(--text); font-weight: 500; }
.file-empty { font-size: 12px; color: var(--text-3); }

.strategy-preview {
  margin: 8px 0 12px; padding: 9px 12px; border-radius: 8px;
  border: 1px dashed #b8d0f5; background: #f5f9ff;
  display: flex; flex-direction: column; gap: 2px;
}
.strategy-preview.empty { background: #fafbfc; color: var(--text-3); font-size: 12px; }
.sp-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.sp-tag { padding: 1px 5px; border-radius: 3px; background: var(--primary); color: #fff; font-size: 11px; }
.sp-main strong { font-size: 13px; color: var(--text); }
.sp-unit { font-size: 11px; color: var(--text-3); }
.sp-boundary { font-size: 12px; color: var(--text-2); }
.sp-note { font-size: 11px; color: var(--text-3); line-height: 1.5; }

.cat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
.cat-opt, .tier-opt {
  position: relative; cursor: pointer;
  border: 1.5px solid var(--border); border-radius: 8px;
  padding: 10px 12px; background: #fff;
  transition: border-color 0.15s, background 0.15s;
  display: flex; flex-direction: column; gap: 4px;
}
.cat-opt:hover, .tier-opt:hover { border-color: #b8c8e4; }
.cat-opt.active, .tier-opt.active { border-color: var(--primary); background: #f7faff; }
.cat-opt input, .tier-opt input { position: absolute; opacity: 0; pointer-events: none; }
.cat-opt-head { display: flex; align-items: center; gap: 8px; }
.cat-opt-name { font-size: 13px; font-weight: 600; color: var(--text); }
.cat-opt-tag {
  padding: 1px 6px; border-radius: 4px; font-size: 11px;
  background: var(--primary-light); color: var(--primary);
}
.cat-opt-size { font-size: 11px; color: var(--text-3); }
.cat-opt-desc { font-size: 11px; color: var(--text-2); line-height: 1.55; }
.cat-opt-desc.note { color: var(--text-3); }
.dlg-foot-note { margin-top: 10px; font-size: 11px; color: var(--text-3); line-height: 1.65; }
.cat-opt-count { margin-top: 2px; font-size: 11px; color: var(--text-3); display: flex; gap: 8px; }
.cat-strategy-key { font-family: ui-monospace, Consolas, monospace; color: #9aa7bd; }

.formula-bar {
  padding: 10px 12px; border-radius: 8px;
  background: #f5f8ff; border: 1px solid #dbe6fb;
  font-size: 12px; color: var(--text-2); line-height: 1.6;
}
.formula-bar b { color: var(--primary); font-weight: 600; }
.formula-sub { margin-top: 4px; font-size: 11px; color: var(--text-3); }

.tier-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
.tier-opt.tier-core { border-left: 3px solid #f0a35e; }
.tier-opt.tier-standard { border-left: 3px solid #67c48b; }
.tier-opt.tier-archived { border-left: 3px solid #b7bfcc; }
.tier-opt-head { display: flex; align-items: center; justify-content: space-between; }
.tier-opt-name { font-size: 13px; font-weight: 600; color: var(--text); }
.tier-weight { font-size: 12px; font-weight: 600; color: var(--primary); }
.tier-opt-desc { font-size: 11px; color: var(--text-2); line-height: 1.55; }

.importance-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.importance-label { font-size: 12px; color: var(--text-2); }
.importance-row .dlg-hint { margin-left: 2px; }

.meta-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px 18px; }
.meta-item { display: flex; flex-direction: column; gap: 5px; }
.meta-item label { font-size: 12px; font-weight: 500; color: var(--text-2); }
.meta-item input, .meta-item select { padding: 6px 9px; font-size: 12px; border-radius: 6px; }
.meta-readonly {
  padding: 6px 9px; font-size: 12px; color: var(--text-3);
  background: #fafbfd; border: 1px dashed var(--border); border-radius: 6px; line-height: 1.6;
}

.dialog-foot {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 12px 20px; border-top: 1px solid var(--border); background: #fafbfd;
}
.foot-hint { font-size: 12px; color: var(--text-3); }
.foot-actions { margin-left: auto; display: flex; gap: 10px; }
.dialog-foot .upload-progress { flex: 1; max-width: 420px; }
.dialog-foot .upload-msg { flex: 1; }
</style>
