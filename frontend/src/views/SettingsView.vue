<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2>设置</h2>
        <div class="sub">大模型、向量化、联网 Key 与检索参数保存后立即生效，无需重启后端</div>
      </div>
    </div>

    <nav class="sub-tabs">
      <button
        v-for="t in subTabs"
        :key="t.key"
        :class="{ active: subTab === t.key }"
        @click="subTab = t.key"
      >{{ t.label }}</button>
    </nav>

    <!-- 模型服务：大模型 / 向量化 / 联网 Key / 检索分块 -->
    <template v-if="subTab === 'model'">
      <section v-for="g in modelGroups" :key="g.key" class="card">
        <div class="card-head">
          <div>
            <h3>{{ g.title }}</h3>
            <p>{{ g.desc }}</p>
          </div>
          <div class="actions" v-if="g.key === 'llm'">
            <button
              v-for="p in presets"
              :key="p.name"
              class="btn mini"
              @click="applyPreset(p)"
            >{{ p.name }}</button>
          </div>
        </div>

        <div class="form-grid">
          <div class="form-row" v-for="item in groupItems(g)" :key="item.key">
            <label>
              {{ item.label }}
              <span v-if="item.unit" class="unit">（{{ item.unit }}）</span>
            </label>
            <div class="field">
              <select v-if="item.options" v-model="modelForm[item.key]">
                <option v-for="o in item.options" :key="o" :value="o">{{ o }}</option>
              </select>
              <input
                v-else-if="item.type === 'int' || item.type === 'float'"
                type="number"
                :step="item.step || 1"
                :min="item.min"
                :max="item.max"
                v-model.number="modelForm[item.key]"
              />
              <input
                v-else-if="item.secret"
                type="password"
                autocomplete="off"
                :placeholder="item.configured ? '已配置：清空后保存即删除' : '未配置，可填写后保存'"
                v-model="modelForm[item.key]"
              />
              <input
                v-else
                :placeholder="item.default != null ? String(item.default) : ''"
                v-model="modelForm[item.key]"
              />
              <div class="hint">
                {{ item.hint }}
                <span v-if="item.source === 'custom'" class="badge">已自定义</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <div class="card-foot sticky">
        <button class="btn primary" :disabled="modelSaving" @click="saveModel">
          {{ modelSaving ? '保存中…' : '保存模型与检索配置' }}
        </button>
        <span class="foot-msg">{{ modelMsg }}</span>
      </div>

      <div v-if="rebuildRequired" class="tips warn rebuild-bar">
        <span>向量化或分块配置已变更：已有切片仍是旧配置的结果，需要重建一次索引。</span>
        <button class="btn" :disabled="rebuilding" @click="rebuildVectors">
          {{ rebuilding ? '重建中…' : '立即重建向量索引' }}
        </button>
        <span class="foot-msg">{{ rebuildMsg }}</span>
      </div>
    </template>

    <!-- 联网搜索 -->
    <section v-show="subTab === 'web'" class="card">
      <div class="card-head">
        <div>
          <p>开启后，提问会调用 Tavily 搜索网络，把网页内容并入回答上下文并标注来源。</p>
        </div>
        <label class="switch">
          <input type="checkbox" v-model="web.enabled" />
          <span class="slider"></span>
        </label>
      </div>

      <div class="row" v-if="web.enabled">
        <label>搜索时机</label>
        <select v-model="web.mode">
          <option value="always">每次提问都联网</option>
          <option value="fallback">仅本地知识库未命中时联网（更省额度）</option>
        </select>
      </div>
      <div class="row" v-if="web.enabled">
        <label>返回网页条数</label>
        <input type="number" min="1" max="10" v-model.number="web.maxResults" />
        <span class="unit">1 ~ 10 条</span>
      </div>

      <div class="tips" :class="{ warn: !system.tavily_key_configured }">
        <template v-if="system.tavily_key_configured">
          Tavily Key 已配置；如需更换 Key / 接口地址，请在「模型服务」中修改
        </template>
        <template v-else>
          未配置 Tavily Key：请到「模型服务」填写 API Key 后保存，开关可先保存
        </template>
      </div>

      <div class="card-foot">
        <button class="btn primary" :disabled="webSaving" @click="saveWeb">
          {{ webSaving ? '保存中…' : '保存联网设置' }}
        </button>
        <span class="foot-msg">{{ webMsg }}</span>
      </div>
    </section>

    <!-- 文档分类（知识库） -->
    <section v-show="subTab === 'doc'" class="card">
      <div class="card-head">
        <div>
          <p>用于文档管理页的归类与筛选；改名/改色保存后立即生效，删除分类会把其下文档变为「未分类」。</p>
        </div>
        <div class="actions">
          <button class="btn" @click="loadDocCategories">重置</button>
          <button class="btn" @click="addDocRow">新增分类</button>
          <button class="btn primary" :disabled="docSaving" @click="saveDocCategories">
            {{ docSaving ? '保存中…' : '保存全部' }}
          </button>
        </div>
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th style="width: 170px">名称</th>
              <th style="width: 60px">颜色</th>
              <th>说明（选填）</th>
              <th style="width: 76px">文档数</th>
              <th style="width: 56px"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in docCategories" :key="c.id || c._tmp">
              <td><input v-model="c.name" /></td>
              <td><input type="color" v-model="c.color" /></td>
              <td><input v-model="c.description" placeholder="如：产品手册、政策制度" /></td>
              <td class="center count">{{ c.id ? (c.doc_count || 0) : '新' }}</td>
              <td><button class="link danger" @click="removeDocRow(c)">删除</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="tips">文档分类只影响「文档管理」的归类与统计，与下面的「问答分类」是两套独立体系。</div>
    </section>

    <!-- 问答分类 -->
    <section v-show="subTab === 'qa'" class="card">
      <div class="card-head">
        <div>
          <p>用户问题按顺序匹配关键词，命中第一个分类即归档；「其他」为兜底分类，不可删除。</p>
        </div>
        <div class="actions">
          <button class="btn" @click="load">重置</button>
          <button class="btn" @click="addRow">新增分类</button>
          <button class="btn primary" :disabled="saving" @click="saveCategories">
            {{ saving ? '保存中…' : '保存全部' }}
          </button>
        </div>
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th style="width: 64px">顺序</th>
              <th style="width: 130px">名称</th>
              <th style="width: 60px">颜色</th>
              <th>关键词（英文逗号分隔）</th>
              <th style="width: 56px">启用</th>
              <th style="width: 56px"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in categories" :key="c.id || c._tmp">
              <td><input class="num" type="number" v-model.number="c.sort_order" /></td>
              <td><input v-model="c.label" /></td>
              <td><input type="color" v-model="c.color" /></td>
              <td>
                <input
                  class="kw"
                  v-model="c.keywords"
                  :disabled="c.id === 'other'"
                  :placeholder="c.id === 'other' ? '兜底分类无需关键词' : '如：报错,失败,无法'"
                />
              </td>
              <td class="center">
                <input type="checkbox" v-model="c.enabled" :disabled="c.id === 'other'" />
              </td>
              <td>
                <button v-if="c.id !== 'other'" class="link danger" @click="removeRow(c)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="tips">
        顺序即优先级（数字越小越先匹配）；新增分类会自动生成内部标识，删除分类会把已有会话归入「其他」。
      </div>
    </section>

    <!-- 系统信息 -->
    <section v-show="subTab === 'system'" class="card">
      <div class="info-grid">
        <div><span>大模型</span><b>{{ system.llm_model || '-' }}</b></div>
        <div><span>模型接口</span><b>{{ system.llm_base_url || '-' }}</b></div>
        <div><span>向量化方式</span><b>{{ system.embedding_provider || '-' }}</b></div>
        <div><span>向量模型</span><b>{{ system.embedding_model || '-' }}</b></div>
        <div><span>数据库</span><b>{{ system.database || '-' }}</b></div>
        <div><span>向量库</span><b>{{ system.vector_backend || '-' }}</b></div>
        <div><span>LLM Key</span><b>{{ system.llm_api_key_configured ? '已配置' : '未配置' }}</b></div>
        <div><span>向量 Key</span><b>{{ system.embedding_api_key_configured ? '已配置（可复用大模型 Key）' : '未配置' }}</b></div>
        <div><span>Tavily Key</span><b>{{ system.tavily_key_configured ? '已配置' : '未配置' }}</b></div>
        <div><span>单文件上限</span><b>{{ system.max_file_size_mb }} MB</b></div>
        <div><span>数据目录</span><b>{{ system.data_dir || '-' }}</b></div>
        <div><span>初始文档目录</span><b>{{ system.doc_dir || '-' }}</b></div>
      </div>
      <div class="tips">
        以上为当前实际生效的配置（管理端保存值优先，未设置的项回退 backend/.env）。
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'

const subTabs = [
  { key: 'model', label: '模型服务' },
  { key: 'web', label: '联网搜索' },
  { key: 'doc', label: '文档分类' },
  { key: 'qa', label: '问答分类' },
  { key: 'system', label: '系统信息' }
]
const subTab = ref('model')

const modelGroups = [
  {
    key: 'llm',
    title: '大模型（问答生成）',
    desc: 'OpenAI 兼容协议，可切换任意厂商：填 Key、接口地址与模型名即可，保存后新提问立即使用新模型。',
    keys: ['llm_api_key', 'llm_base_url', 'llm_model', 'llm_temperature']
  },
  {
    key: 'embedding',
    title: '向量化模型',
    desc: '决定文档切片如何转为向量。更换 provider 或模型名后，必须重建一次向量索引。',
    keys: [
      'embedding_provider', 'embedding_base_url', 'embedding_api_model',
      'embedding_api_key', 'embedding_local_model', 'embedding_batch_size'
    ]
  },
  {
    key: 'search',
    title: '联网搜索（Tavily）',
    desc: '联网 Key 与接口地址；开关与条数在「联网搜索」Tab 中设置。',
    keys: ['tavily_api_key', 'tavily_base_url']
  },
  {
    key: 'retrieval',
    title: '检索与分块',
    desc: '控制切片大小与召回质量；回答太慢或无关内容太多时，可上调相似度阈值。',
    keys: ['chunk_size', 'chunk_overlap', 'top_k', 'score_threshold']
  }
]

const presets = [
  { name: '阿里云百炼', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen3.8-27b' },
  { name: 'DeepSeek', base: 'https://api.deepseek.com', model: 'deepseek-chat' },
  { name: '硅基流动', base: 'https://api.siliconflow.cn/v1', model: 'Qwen/Qwen2.5-7B-Instruct' }
]

const web = ref({ enabled: false, mode: 'fallback', maxResults: 5 })
const system = ref({})
const editable = ref({})
const modelForm = ref({})
const originalForm = ref({})
const categories = ref([])
const docCategories = ref([])
const saving = ref(false)
const webSaving = ref(false)
const docSaving = ref(false)
const modelSaving = ref(false)
const webMsg = ref('')
const modelMsg = ref('')
const rebuildRequired = ref(false)
const rebuilding = ref(false)
const rebuildMsg = ref('')
// 这些配置变化会改变切片结果/向量空间，保存时后端会自动重建文档分块
const REBUILD_KEYS = [
  'chunk_size', 'chunk_overlap',
  'embedding_provider', 'embedding_base_url', 'embedding_api_model', 'embedding_local_model',
]

function pick(editable, key) {
  const item = (editable || []).find(i => i.key === key)
  return item ? item.value : undefined
}

function groupItems(group) {
  return group.keys
    .map(k => editable.value[k])
    .filter(Boolean)
    .filter(item => {
      if (item.key === 'embedding_local_model') return modelForm.value.embedding_provider === 'local'
      if (['embedding_base_url', 'embedding_api_model', 'embedding_api_key'].includes(item.key)) {
        return modelForm.value.embedding_provider === 'openai'
      }
      return true
    })
}

function applyPreset(p) {
  modelForm.value.llm_base_url = p.base
  modelForm.value.llm_model = p.model
}

async function load() {
  try {
    const s = await api.getSettings()
    system.value = s.system || {}
    editable.value = Object.fromEntries((s.editable || []).map(i => [i.key, i]))
    const form = {}
    for (const item of s.editable || []) {
      // 密钥默认回显系统已有值（管理端保存过则取保存值，否则取 .env；都没有为空）
      form[item.key] = item.value ?? ''
    }
    modelForm.value = form
    originalForm.value = { ...form }
    web.value = {
      enabled: !!pick(s.editable, 'web_search_enabled'),
      mode: pick(s.editable, 'web_search_mode') || 'fallback',
      maxResults: pick(s.editable, 'web_search_max_results') ?? 5
    }
    categories.value = (await api.qaCategories()).map(c => ({ ...c }))
    await loadDocCategories()
  } catch (e) {
    webMsg.value = '加载失败：' + e.message
    modelMsg.value = '加载失败：' + e.message
  }
}

async function saveModel() {
  modelSaving.value = true
  modelMsg.value = ''
  rebuildMsg.value = ''
  const payload = {}
  try {
    for (const g of modelGroups) {
      for (const k of g.keys) {
        const item = editable.value[k]
        if (!item) continue
        const value = modelForm.value[k]
        if (item.secret) {
          // 未改动不提交；清空（删除 Key）也要提交，后端按空值清除该 Key
          const next = value ?? ''
          if (String(next) !== String(originalForm.value[k] ?? '')) payload[k] = next
          continue
        }
        // 只提交发生变化的项，未修改的继续沿用 .env 默认值
        if (String(value) !== String(originalForm.value[k])) payload[k] = value
      }
    }
    const res = await api.updateSettings(payload)
    await load()
    rebuildRequired.value = false
    const r = res.rebuild_result
    modelMsg.value = r
      ? `已保存，并按新配置重建了 ${r.rebuilt}/${r.total} 篇文档的分块`
      : '已保存，立即生效'
  } catch (e) {
    modelMsg.value = '保存失败：' + e.message
    // 分块/向量化配置已落库但重建失败：刷新显示新值，并给出「立即重建」重试入口
    if (REBUILD_KEYS.some(k => k in payload)) {
      rebuildRequired.value = true
      await load()
    }
  } finally {
    modelSaving.value = false
  }
}

async function rebuildVectors() {
  if (!confirm('将按当前向量化配置重新处理全部文档，期间切片会被重建，确定继续？')) return
  rebuilding.value = true
  rebuildMsg.value = ''
  try {
    const r = await api.rebuildVectors()
    rebuildRequired.value = false
    const failed = r.failed || []
    rebuildMsg.value = `重建完成：成功 ${r.rebuilt}/${r.total}`
      + (failed.length ? `，失败 ${failed.length}（${failed[0].filename}：${failed[0].error}）` : '')
  } catch (e) {
    rebuildMsg.value = '重建失败：' + e.message
  } finally {
    rebuilding.value = false
  }
}

async function saveWeb() {
  webSaving.value = true
  webMsg.value = ''
  try {
    await api.updateSettings({
      web_search_enabled: web.value.enabled,
      web_search_mode: web.value.mode,
      web_search_max_results: web.value.maxResults
    })
    await load()
    webMsg.value = '已保存，立即生效'
  } catch (e) {
    webMsg.value = '保存失败：' + e.message
  } finally {
    webSaving.value = false
  }
}

// ---------- 文档分类（知识库） ----------
async function loadDocCategories() {
  try {
    docCategories.value = (await api.listCategories()).map(c => ({ ...c }))
  } catch (e) {
    alert('文档分类加载失败：' + e.message)
  }
}

function addDocRow() {
  docCategories.value.push({
    id: '',
    name: '',
    description: '',
    color: '#165dff',
    _tmp: Date.now()
  })
}

async function removeDocRow(c) {
  const warn = c.doc_count ? `该分类下有 ${c.doc_count} 个文档，删除后它们会变为「未分类」。` : ''
  if (!confirm(`删除文档分类「${c.name || '未命名'}」？${warn}`)) return
  if (!c.id) {
    docCategories.value = docCategories.value.filter(x => x !== c)
    return
  }
  try {
    await api.deleteCategory(c.id)
    await loadDocCategories()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

async function saveDocCategories() {
  docSaving.value = true
  try {
    for (const c of docCategories.value) {
      const name = (c.name || '').trim()
      if (!name) continue
      const payload = { name, description: c.description || '', color: c.color }
      if (c.id) {
        await api.updateCategory(c.id, payload)
      } else {
        await api.createCategory(payload)
      }
    }
    await loadDocCategories()
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    docSaving.value = false
  }
}

function addRow() {
  const maxOrder = categories.value.reduce((m, c) => Math.max(m, c.sort_order || 0), 0)
  categories.value.push({
    id: '',
    label: '新分类',
    color: '#165dff',
    keywords: '',
    sort_order: maxOrder + 1,
    enabled: true,
    _tmp: Date.now()
  })
}

async function removeRow(c) {
  if (!confirm(`删除分类「${c.label}」？该分类下的历史会话将归入「其他」。`)) return
  try {
    await api.deleteQaCategory(c.id)
    await load()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

async function saveCategories() {
  saving.value = true
  try {
    for (const c of categories.value) {
      const payload = {
        label: c.label,
        color: c.color,
        keywords: c.keywords,
        sort_order: c.sort_order,
        enabled: c.enabled
      }
      if (!c.id) {
        await api.createQaCategory(payload)
      } else {
        await api.updateQaCategory(c.id, payload)
      }
    }
    await load()
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page { padding: 20px; height: 100%; overflow-y: auto; display: flex; flex-direction: column; gap: 16px; }
.page-header h2 { font-size: 18px; }
.page-header .sub { font-size: 12px; color: var(--text-3); margin-top: 4px; }

.card { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px; }
.card p { font-size: 12px; color: var(--text-3); line-height: 1.6; }
.card h3 { font-size: 14px; margin-bottom: 4px; }
.card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 10px; }

.sub-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); }
.sub-tabs button {
  padding: 9px 16px; border: none; background: none; cursor: pointer;
  font-size: 13px; color: var(--text-2); border-bottom: 2px solid transparent;
}
.sub-tabs button:hover { color: var(--primary); }
.sub-tabs button.active { color: var(--primary); font-weight: 600; border-bottom-color: var(--primary); }

.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 24px; }
.form-row { display: flex; flex-direction: column; gap: 6px; }
.form-row > label { font-size: 13px; color: var(--text-2); }
.form-row .unit { font-size: 12px; color: var(--text-3); }
.field select, .field input {
  width: 100%; padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px;
  font-size: 13px; box-sizing: border-box;
}
.hint { font-size: 12px; color: var(--text-3); line-height: 1.5; margin-top: 4px; }
.badge {
  margin-left: 6px; padding: 1px 6px; border-radius: 4px;
  background: #e8f3ff; color: var(--primary); font-size: 11px;
}

.switch { position: relative; display: inline-block; width: 46px; height: 24px; flex-shrink: 0; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider {
  position: absolute; inset: 0; background: #cfd6e4; border-radius: 999px;
  transition: 0.2s; cursor: pointer;
}
.slider::before {
  content: ''; position: absolute; width: 18px; height: 18px; left: 3px; top: 3px;
  background: #fff; border-radius: 50%; transition: 0.2s;
}
.switch input:checked + .slider { background: var(--primary); }
.switch input:checked + .slider::before { transform: translateX(22px); }

.row { display: flex; align-items: center; gap: 12px; margin: 10px 0; }
.row label { width: 110px; font-size: 13px; color: var(--text-2); }
.row select, .row input {
  padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px;
}
.row input[type='number'] { width: 90px; }
.row .unit { font-size: 12px; color: var(--text-3); }

.tips { font-size: 12px; color: var(--text-3); background: #f6f8fc; border-radius: 6px; padding: 8px 10px; margin-top: 10px; }
.tips.warn { color: #b25c00; background: #fff5e6; }
.rebuild-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

.card-foot { display: flex; align-items: center; gap: 12px; margin-top: 14px; }
.card-foot.sticky {
  position: sticky; bottom: 0; z-index: 2; padding: 12px 0;
  background: linear-gradient(to top, var(--bg) 70%, transparent);
}
.foot-msg { font-size: 12px; color: var(--success); }

.actions { display: flex; gap: 8px; flex-shrink: 0; flex-wrap: wrap; justify-content: flex-end; }
.btn {
  padding: 7px 14px; border: 1px solid var(--border); background: #fff;
  border-radius: 6px; cursor: pointer; font-size: 13px;
}
.btn.mini { padding: 4px 10px; font-size: 12px; }
.btn:hover { border-color: var(--primary); color: var(--primary); }
.btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.btn.primary:hover { opacity: 0.9; color: #fff; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }

.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; font-size: 12px; color: var(--text-3); font-weight: 500; padding: 8px; border-bottom: 1px solid var(--border); }
td { padding: 7px 8px; border-bottom: 1px solid #f2f4f8; }
td input:not([type='color']):not([type='checkbox']) {
  width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 5px; font-size: 13px;
}
td input.kw { min-width: 320px; }
td input.num { width: 62px; }
td input[type='color'] { width: 34px; height: 28px; border: none; background: none; padding: 0; }
td.center { text-align: center; }
td.count { color: var(--text-3); font-size: 12px; }
td.empty { text-align: center; color: var(--text-3); padding: 24px; }
.link { border: none; background: none; color: var(--primary); cursor: pointer; font-size: 12px; padding: 0; }
.link.danger { color: #c9302c; }

.info-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-top: 12px; font-size: 12px; }
.info-grid span { color: var(--text-3); display: block; margin-bottom: 2px; }
.info-grid b { font-weight: 600; word-break: break-all; }
</style>
