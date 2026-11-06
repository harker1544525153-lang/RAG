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
          <!-- 检索模块：先选文档类型，再复用下方形同的分块大小 / 重叠字段 -->
          <template v-if="g.key === 'retrieval'">
            <div class="form-row">
              <label>文档类型</label>
              <div class="field">
                <select v-model="retrievalTarget">
                  <option value="default">默认设置</option>
                  <option v-for="c in docCategories" :key="c.id" :value="c.id">{{ c.name }}</option>
                </select>
                <div class="hint">选「默认设置」编辑全局兜底参数；选具体类型后，下方策略与块大小即显示该类型当前值</div>
              </div>
            </div>
            <div class="form-row">
              <label>分块策略</label>
              <div class="field">
                <select
                  v-if="targetCategory"
                  v-model="targetCategory.strategy"
                  @change="applyStrategyDefaults(targetCategory)"
                >
                  <option v-for="o in STRATEGY_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
                <input v-else value="基础标准（全局兜底）" disabled />
                <div class="hint">
                  {{ targetCategory ? '切换策略会套用该策略的默认块大小' : '默认设置按「基础标准」兜底' }}
                </div>
              </div>
            </div>
          </template>

          <div class="form-row" v-for="item in groupItems(g)" :key="item.key">
            <label>
              {{ item.label }}
              <span v-if="item.unit" class="unit">
                （{{ g.key === 'retrieval' && (item.key === 'chunk_size' || item.key === 'chunk_overlap') ? currentTypeUnit : item.unit }}）
              </span>
            </label>
            <div class="field">
              <select v-if="item.options" v-model="modelForm[item.key]">
                <option v-for="o in item.options" :key="o" :value="o">{{ o }}</option>
              </select>
              <!-- 检索模块：分块大小 / 重叠复用为所选文档类型的参数 -->
              <template v-else-if="g.key === 'retrieval' && item.key === 'chunk_size'">
                <input
                  v-if="targetCategory"
                  type="number" :step="item.step || 1" :min="item.min" :max="item.max"
                  v-model.number="targetCategory.chunk_size"
                />
                <input
                  v-else
                  type="number" :step="item.step || 1" :min="item.min" :max="item.max"
                  v-model.number="modelForm[item.key]"
                />
              </template>
              <template v-else-if="g.key === 'retrieval' && item.key === 'chunk_overlap'">
                <input
                  v-if="targetCategory"
                  type="number" :step="item.step || 1" :min="item.min" :max="item.max"
                  v-model.number="targetCategory.chunk_overlap"
                />
                <input
                  v-else
                  type="number" :step="item.step || 1" :min="item.min" :max="item.max"
                  v-model.number="modelForm[item.key]"
                />
              </template>
              <input
                v-else-if="item.type === 'int' || item.type === 'float'"
                type="number"
                :step="item.step || 1"
                :min="item.min"
                :max="item.max"
                v-model.number="modelForm[item.key]"
              />
              <label v-else-if="item.type === 'bool'" class="switch-inline">
                <input type="checkbox" v-model="modelForm[item.key]" />
                <span>{{ modelForm[item.key] ? '已启用' : '已关闭' }}</span>
              </label>
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

        <div v-if="g.key === 'retrieval'" class="type-tune-foot">
          <button class="btn" :disabled="typeSaving" @click="saveTypeParams">
            {{ typeSaving ? '保存中…' : (targetCategory ? `保存「${targetCategory.name}」的分块参数` : '保存默认分块参数') }}
          </button>
          <span class="foot-msg">{{ typeMsg }}</span>
        </div>
      </section>

      <!-- 按文档类型的分块参数：与「文档分类」Tab 共用同一份分类配置 -->
      <section class="card">
        <div class="card-head">
          <div>
            <h3>各文档类型的分块参数（只读）</h3>
            <p>
              仅用于查看当前生效值；如需修改，请在上方「检索、分级与时间权重」的「文档类型」下拉中选择对应类型后编辑。
              上传时选「默认配置」，系统识别出文档类型后即按这里的参数切分。
            </p>
          </div>
        </div>

        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th style="width: 180px">文档类型</th>
                <th style="width: 150px">分块策略</th>
                <th style="width: 132px">目标块大小</th>
                <th style="width: 112px">重叠</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in docCategories" :key="c.id || c._tmp">
                <td class="type-name">
                  <span class="type-dot" :style="{ background: c.color }"></span>{{ c.name }}
                </td>
                <td class="read-cell">{{ strategyLabel(c.strategy) }}</td>
                <td class="read-cell">{{ c.chunk_size }} {{ strategyUnit(c.strategy) }}</td>
                <td class="read-cell">{{ c.chunk_overlap }} {{ strategyUnit(c.strategy) }}</td>
                <td class="type-desc">{{ c.description || '—' }}</td>
              </tr>
              <tr v-if="!docCategories.length">
                <td colspan="5" class="empty">暂无文档类型，可在「文档分类」Tab 新增</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="tips">
          目标块大小是「目标上限」而非硬性最大值：累计达到目标即切分，遇到标题 / 条款 / 步骤 / 页码边界会提前断开，
          超长内容二次切分时保留设定的重叠。上传时手动指定分类，则直接使用该分类的这组参数。
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
          <p>用于文档管理页的归类与筛选；改名/改色保存后立即生效，删除分类会把其下文档变为「未分类」。分块策略与块大小在「模型服务 → 检索、分级与时间权重」中按文档类型编辑，此处仅展示。</p>
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
              <th style="width: 150px">名称</th>
              <th style="width: 52px">颜色</th>
              <th style="width: 128px">分块策略</th>
              <th style="width: 88px">目标块大小</th>
              <th style="width: 64px">重叠</th>
              <th>说明（选填）</th>
              <th style="width: 66px">文档数</th>
              <th style="width: 52px"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in docCategories" :key="c.id || c._tmp">
              <td><input v-model="c.name" /></td>
              <td><input type="color" v-model="c.color" /></td>
              <td class="read-cell">{{ strategyLabel(c.strategy) }}</td>
              <td class="read-cell">{{ c.chunk_size }} {{ strategyUnit(c.strategy) }}</td>
              <td class="read-cell">{{ c.chunk_overlap }} {{ strategyUnit(c.strategy) }}</td>
              <td><input v-model="c.description" placeholder="如：制度条款、产品手册" /></td>
              <td class="center count">{{ c.id ? (c.doc_count || 0) : '新' }}</td>
              <td><button class="link danger" @click="removeDocRow(c)">删除</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="tips">
        分类决定上传文档的分块方式：按 Token 数（块大小以 Token 计）、基础标准（512 Token 基线 + 10%~15% 重叠）、
        语义分块（标题/条款边界）、段落结构（标题 + 段落，适配 Word/TXT）、按页切分（同页不跨页，适配 PDF/PPT）、
        层级分块（章节摘要 + 子块）、代码结构（按函数/类）、表格结构（重复表头按行组）、流程结构（按「第 X 步 / ①」等步骤边界）。
        块大小是「目标上限」而非硬性最大值：累计达到目标即切分，遇到标题 / 条款 / 步骤 / 页码等边界会提前断开；
        超长内容二次切分时相邻块保留设定的重叠字符（768 字符 ≈ 512 Token）。单位按字符计（「按 Token 数」策略下按 Token 计）。
        如需修改策略或块大小，请到「模型服务 → 检索、分级与时间权重」的「文档类型」下拉中选择对应类型后编辑；
        修改后已有文档需在「文档管理」中点「仅重建切片」才会生效。
      </div>
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

    <!-- 信息分类：品牌标识（Logo / 名称），随账号独立保存 -->
    <section v-show="subTab === 'brand'" class="brand-panel">
      <section v-for="b in brandBlocks" :key="b.kind" class="card">
        <div class="card-head">
          <div>
            <h3>{{ b.title }}</h3>
            <p>{{ b.desc }}</p>
          </div>
        </div>

        <div class="brand-editor">
          <div class="logo-box">
            <img v-if="modelForm[b.logoKey]" :src="modelForm[b.logoKey]" :alt="b.title" />
            <span v-else class="logo-fallback">{{ b.fallback }}</span>
          </div>
          <div class="logo-ops">
            <input
              :id="`logo-file-${b.kind}`"
              type="file"
              accept="image/png,image/jpeg,image/svg+xml,image/webp"
              hidden
              @change="pickLogo(b.kind, $event)"
            />
            <label :for="`logo-file-${b.kind}`" class="btn mini">上传 Logo</label>
            <button class="btn mini" @click="resetLogo(b.kind)">恢复默认</button>
            <div class="hint">支持 PNG / JPG / SVG / WEBP，不超过 2MB；上传后立即生效</div>
          </div>
        </div>

        <div class="form-grid">
          <div class="form-row">
            <label>名称</label>
            <div class="field">
              <input v-model="modelForm[b.titleKey]" :placeholder="b.defaultTitle" />
              <div class="hint">留空则使用内置默认名称</div>
            </div>
          </div>
          <div class="form-row">
            <label>副标题</label>
            <div class="field">
              <input v-model="modelForm[b.subtitleKey]" :placeholder="b.defaultSubtitle" />
              <div class="hint">显示在名称下方的一句话说明</div>
            </div>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <div>
            <h3>用户端问答链接</h3>
            <p>发给访客即可免登录问答；回答只使用本账号的文档与模型服务配置，不同账号链接互不影响。</p>
          </div>
        </div>
        <div class="widget-link-row">
          <input class="input" :value="widgetUrl" readonly placeholder="正在生成…" />
          <button class="btn" @click="copyWidgetUrl">复制链接</button>
        </div>
        <p class="field-hint" v-if="widgetMsg">{{ widgetMsg }}</p>
      </section>

      <div class="card-foot sticky">
        <button class="btn primary" :disabled="brandSaving" @click="saveBrand">
          {{ brandSaving ? '保存中…' : '保存信息分类' }}
        </button>
        <span class="foot-msg">{{ brandMsg }}</span>
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
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'

const subTabs = [
  { key: 'model', label: '模型服务' },
  { key: 'web', label: '联网搜索' },
  { key: 'doc', label: '文档分类' },
  { key: 'qa', label: '问答分类' },
  { key: 'brand', label: '信息分类' },
  { key: 'system', label: '系统信息' }
]
const subTab = ref('model')

// 信息分类：后台管理 / 用户端 两套品牌标识，按当前账号保存
const brandBlocks = [
  {
    kind: 'admin',
    title: '后台管理标识',
    desc: '管理端侧边栏顶部的 Logo、名称与副标题；仅影响当前账号的后台界面。',
    logoKey: 'brand_admin_logo',
    titleKey: 'brand_admin_title',
    subtitleKey: 'brand_admin_subtitle',
    defaultTitle: 'GALA 管理后台',
    defaultSubtitle: '企业级 RAG 智能文档问答系统',
    fallback: 'GALA'
  },
  {
    kind: 'user',
    title: '用户端标识',
    desc: '用户端问答页顶部的 Logo、名称与副标题；登录用户看到自己账号的品牌配置。',
    logoKey: 'brand_user_logo',
    titleKey: 'brand_user_title',
    subtitleKey: 'brand_user_subtitle',
    defaultTitle: 'GALA 智能问答助手',
    defaultSubtitle: '基于企业知识库的智能问答',
    fallback: 'GALA'
  }
]
const brandSaving = ref(false)
const brandMsg = ref('')

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
    title: '检索、分级与时间权重',
    desc: '分级权重让核心权威资料优先命中，时间权重让新文档优先；选择「文档类型」后，下方分块策略 / 块大小 / 重叠会显示该类型的当前值。',
    keys: [
      'chunk_size', 'chunk_overlap', 'top_k', 'score_threshold', 'hybrid_search_enabled',
      'tier_core_weight', 'tier_standard_weight', 'tier_archived_weight',
      'recency_half_life_days', 'recency_floor', 'filter_expired'
    ]
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
// 检索模块内按文档类型编辑：默认设置 = 全局兜底参数
const retrievalTarget = ref('default')
const typeSaving = ref(false)
const typeMsg = ref('')
const targetCategory = computed(() =>
  retrievalTarget.value === 'default'
    ? null
    : docCategories.value.find(c => c.id === retrievalTarget.value) || null
)
const currentTypeUnit = computed(() => strategyUnit(targetCategory.value?.strategy || 'standard'))
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

async function pickLogo(kind, event) {
  const file = event.target.files && event.target.files[0]
  if (!file) return
  brandSaving.value = true
  brandMsg.value = ''
  try {
    const res = await api.uploadBrandLogo(kind, file)
    modelForm.value[res.key] = res.url
    notifyBrand()
    brandMsg.value = `${kind === 'admin' ? '后台管理' : '用户端'} Logo 已更新`
  } catch (e) {
    brandMsg.value = '上传失败：' + e.message
  } finally {
    brandSaving.value = false
    event.target.value = ''
  }
}

async function resetLogo(kind) {
  brandSaving.value = true
  brandMsg.value = ''
  try {
    const res = await api.resetBrandLogo(kind)
    modelForm.value[res.key] = ''
    notifyBrand()
    brandMsg.value = '已恢复默认 Logo'
  } catch (e) {
    brandMsg.value = '恢复失败：' + e.message
  } finally {
    brandSaving.value = false
  }
}

async function saveBrand() {
  brandSaving.value = true
  brandMsg.value = ''
  try {
    const payload = {}
    for (const b of brandBlocks) {
      payload[b.logoKey] = modelForm.value[b.logoKey] || ''
      payload[b.titleKey] = modelForm.value[b.titleKey] || ''
      payload[b.subtitleKey] = modelForm.value[b.subtitleKey] || ''
    }
    const s = await api.updateSettings(payload)
    for (const item of s.editable || []) modelForm.value[item.key] = item.value ?? ''
    notifyBrand()
    brandMsg.value = '已保存，管理端与用户端立即生效'
  } catch (e) {
    brandMsg.value = '保存失败：' + e.message
  } finally {
    brandSaving.value = false
  }
}

// 当前账号的用户端问答链接：免登录访问，问答只用本账号的文档与模型配置
const widgetUrl = ref('')
const widgetMsg = ref('')

async function loadWidgetLink() {
  try {
    const d = await api.widgetLink()
    widgetUrl.value = window.location.origin + d.url
  } catch {
    widgetUrl.value = ''
  }
}

async function copyWidgetUrl() {
  if (!widgetUrl.value) return
  try {
    await navigator.clipboard.writeText(widgetUrl.value)
    widgetMsg.value = '已复制到剪贴板'
  } catch {
    widgetMsg.value = '复制失败，请手动选择链接复制'
  }
}

function notifyBrand() {
  window.dispatchEvent(new CustomEvent('gala:brand-updated'))
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
  if (web.value.enabled && !system.value.tavily_key_configured) {
    webMsg.value = '请先填写 Tavily Key 并保存，再开启联网搜索'
    return
  }
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
// 分类默认分块参数：策略决定切分方式，块大小按 512 Token 基线（中文约 768 字符）
const STRATEGY_OPTIONS = [
  { value: 'token', label: '按 Token 数', unit: 'Token' },
  { value: 'standard', label: '基础标准', unit: '字符' },
  { value: 'semantic', label: '语义分块', unit: '字符' },
  { value: 'paragraph', label: '段落结构', unit: '字符' },
  { value: 'page', label: '按页切分', unit: '字符' },
  { value: 'hierarchical', label: '层级分块', unit: '字符' },
  { value: 'code', label: '代码结构', unit: '字符' },
  { value: 'table', label: '表格结构', unit: '字符' },
  { value: 'flow', label: '流程结构', unit: '字符' },
]
function strategyUnit(strategy) {
  return (STRATEGY_OPTIONS.find(o => o.value === strategy) || {}).unit || '字符'
}
function strategyLabel(strategy) {
  return (STRATEGY_OPTIONS.find(o => o.value === strategy) || {}).label || '基础标准'
}

const STRATEGY_DEFAULTS = {
  token: { size: 512, overlap: 64 },
  standard: { size: 768, overlap: 96 },
  semantic: { size: 768, overlap: 120 },
  paragraph: { size: 768, overlap: 0 },
  page: { size: 768, overlap: 0 },
  hierarchical: { size: 768, overlap: 96 },
  code: { size: 1200, overlap: 120 },
  table: { size: 768, overlap: 0 },
  flow: { size: 768, overlap: 96 },
}
function defaultsByStrategy(strategy) {
  return STRATEGY_DEFAULTS[strategy || 'standard'] || STRATEGY_DEFAULTS.standard
}

// 切换策略时套用该策略的默认块大小与重叠，避免残留上一策略的参数
function applyStrategyDefaults(c) {
  const d = defaultsByStrategy(c.strategy)
  c.chunk_size = d.size
  c.chunk_overlap = d.overlap
}

// 保存检索模块中当前所选文档类型的分块参数
async function saveTypeParams() {
  typeSaving.value = true
  typeMsg.value = ''
  try {
    if (targetCategory.value) {
      const c = targetCategory.value
      await api.updateCategory(c.id, {
        name: c.name,
        description: c.description || '',
        color: c.color,
        strategy: c.strategy || 'standard',
        chunk_size: Number(c.chunk_size) || defaultsByStrategy(c.strategy).size,
        chunk_overlap: Number(c.chunk_overlap) || 0,
      })
      await loadDocCategories()
      typeMsg.value = `已保存「${c.name}」的分块参数，上传弹窗与文档设置弹窗同步生效`
    } else {
      const res = await api.updateSettings({
        chunk_size: Number(modelForm.value.chunk_size),
        chunk_overlap: Number(modelForm.value.chunk_overlap),
      })
      await load()
      const r = res.rebuild_result
      typeMsg.value = r
        ? `已保存，并按新配置重建了 ${r.rebuilt}/${r.total} 篇文档的分块`
        : '已保存默认分块参数'
    }
  } catch (e) {
    typeMsg.value = '保存失败：' + e.message
  } finally {
    typeSaving.value = false
  }
}

async function loadDocCategories() {
  try {
    docCategories.value = (await api.listCategories()).map(c => ({
      ...c,
      strategy: c.strategy || 'standard',
      chunk_size: c.chunk_size || defaultsByStrategy(c.strategy).size,
      chunk_overlap: c.chunk_overlap ?? defaultsByStrategy(c.strategy).overlap,
    }))
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
    strategy: 'standard',
    chunk_size: defaultsByStrategy('standard').size,
    chunk_overlap: defaultsByStrategy('standard').overlap,
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
      const payload = {
        name,
        description: c.description || '',
        color: c.color,
        strategy: c.strategy || 'standard',
        chunk_size: Number(c.chunk_size) || defaultsByStrategy(c.strategy).size,
        chunk_overlap: Number(c.chunk_overlap) || 0,
      }
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

onMounted(() => {
  load()
  loadWidgetLink()
})
</script>

<style scoped>
.page { padding: 20px; height: 100%; overflow-y: auto; display: flex; flex-direction: column; gap: 16px; }
.page-header h2 { font-size: 18px; }
.page-header .sub { font-size: 12px; color: var(--text-3); margin-top: 4px; }

.card { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px; }
.card p { font-size: 12px; color: var(--text-3); line-height: 1.6; }
.card h3 { font-size: 14px; margin-bottom: 4px; }
.widget-link-row { display: flex; gap: 10px; align-items: center; margin-top: 6px; }
.widget-link-row input {
  flex: 1; min-width: 0; padding: 9px 12px; font-size: 12px;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: #fff; color: var(--text-2);
}
.field-hint { font-size: 12px; color: var(--text-3); margin-top: 6px; }
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
.switch-inline {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 13px; color: var(--text-2); cursor: pointer;
}
.switch-inline input { width: 16px; height: 16px; cursor: pointer; }

/* 信息分类：品牌标识编辑 */
.brand-editor { display: flex; align-items: center; gap: 18px; margin: 4px 0 18px; }
.logo-box {
  width: 76px; height: 76px; flex: 0 0 auto; border-radius: 16px;
  border: 1px dashed var(--line); background: var(--bg-2, #f7f8fa);
  display: flex; align-items: center; justify-content: center; overflow: hidden;
}
.logo-box img { max-width: 100%; max-height: 100%; object-fit: contain; }
.logo-fallback {
  font-size: 13px; font-weight: 700; color: var(--text-3);
  letter-spacing: .5px;
}
.logo-ops { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.logo-ops .hint { flex-basis: 100%; }

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

/* 按文档类型分块参数表 */
.type-name { display: flex; align-items: center; gap: 6px; font-weight: 500; color: var(--text); }
.type-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.type-desc { font-size: 12px; color: var(--text-3); }
.read-cell { font-size: 12px; color: var(--text-2); }

/* 检索模块内的文档类型参数保存行 */
.type-tune-foot { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 10px; }

.info-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-top: 12px; font-size: 12px; }
.info-grid span { color: var(--text-3); display: block; margin-bottom: 2px; }
.info-grid b { font-weight: 600; word-break: break-all; }
</style>
