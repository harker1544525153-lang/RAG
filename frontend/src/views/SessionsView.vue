<template>
  <div class="page">
    <div class="page-header">
      <h2>问答记录</h2>
      <div class="filters">
        <span class="hint">每 10 秒自动刷新</span>
        <button @click="refreshAll">刷新</button>
        <select v-model="filter">
          <option value="">全部来源</option>
          <option value="user">用户端</option>
          <option value="admin">管理端</option>
        </select>
        <input v-model="keyword" placeholder="搜索会话标题" />
        <button v-if="filter || keyword || categoryFilter" @click="filter=''; keyword=''; categoryFilter=''">清除</button>
      </div>
    </div>

    <div class="stats-row" v-if="stats">
      <div class="stat-card">
        <div class="num">{{ stats.total_sessions }}</div>
        <div class="label">总会话</div>
      </div>
      <div class="stat-card user">
        <div class="num">{{ stats.user_sessions }}</div>
        <div class="label">用户端会话</div>
      </div>
      <div class="stat-card admin">
        <div class="num">{{ stats.admin_sessions }}</div>
        <div class="label">管理端会话</div>
      </div>
      <div class="stat-card msg">
        <div class="num">{{ stats.total_messages }}</div>
        <div class="label">总消息数</div>
      </div>
    </div>

    <div class="cat-bar" v-if="categories.length">
      <span class="cat-title">问答分类</span>
      <button
        class="cat-chip"
        :class="{ active: !categoryFilter }"
        @click="categoryFilter = ''"
      >
        全部 {{ stats.total_sessions }}
      </button>
      <button
        v-for="c in categories"
        :key="c.id"
        class="cat-chip"
        :class="{ active: categoryFilter === c.id }"
        :style="{ '--cat': c.color }"
        @click="categoryFilter = categoryFilter === c.id ? '' : c.id"
      >
        {{ c.label }} {{ stats.category_counts[c.id] || 0 }}
      </button>
    </div>

    <div class="layout">
      <aside class="session-list">
        <div v-if="!sessions.length" class="empty">暂无会话记录</div>
        <div
          v-for="s in filtered"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === currentId }"
          @click="openSession(s.id)"
        >
          <div class="title">{{ s.title }}</div>
          <div class="meta">
            <span
              class="cat-tag"
              :style="{ color: s.category_color, borderColor: s.category_color + '55' }"
            >
              {{ s.category_label }}
            </span>
            <span class="source-tag" :class="s.source">
              {{ s.source === 'user' ? '用户' : '管理' }}
            </span>
            <span class="visitor" v-if="s.visitor_name">{{ s.visitor_name }}</span>
            <span class="time">{{ s.created_at }}</span>
          </div>
          <div class="count">{{ s.msg_count }} 条消息</div>
        </div>
      </aside>

      <main class="chat-area" v-if="currentId">
        <div v-if="!messages.length" class="empty">加载中...</div>
        <div v-for="m in messages" :key="m.id" class="chat-line" :class="m.role">
          <div class="bubble">
            <pre>{{ m.content }}</pre>
            <div v-if="m.sources" class="sources-inline">
              📎 {{ m.sources.length }} 个参考来源
            </div>
          </div>
          <div class="time">{{ m.created_at }}</div>
        </div>
      </main>
      <main class="chat-area empty-main" v-else>
        <div class="empty">← 左侧选择一个会话查看详情</div>
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { api } from '../api'

const sessions = ref([])
const stats = ref(null)
const filter = ref('')
const categoryFilter = ref('')
const keyword = ref('')
const currentId = ref('')
const messages = ref([])

const categories = computed(() => stats.value?.categories || [])

const filtered = computed(() => {
  let list = sessions.value
  if (filter.value) list = list.filter(s => s.source === filter.value)
  if (keyword.value) {
    const k = keyword.value.toLowerCase()
    list = list.filter(s => s.title.toLowerCase().includes(k))
  }
  return list
})

async function loadSessions() {
  sessions.value = await api.listSessions(filter.value, categoryFilter.value)
}
async function loadStats() {
  stats.value = await api.sessionStats()
}
async function openSession(id) {
  currentId.value = id
  messages.value = await api.sessionMessages(id)
}

watch(filter, () => loadSessions().catch(() => {}))
watch(categoryFilter, () => loadSessions().catch(() => {}))

let timer = null
async function refreshAll() {
  try {
    await Promise.all([loadSessions(), loadStats()])
  } catch {
    // 后端暂不可用（如服务重启中）时保持上次数据，下一轮自动重试，避免未捕获异常
  }
}
onMounted(() => {
  refreshAll()
  timer = setInterval(refreshAll, 10000)   // 用户端新问答会自动出现在列表
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.page { padding: 20px; height: 100%; display: flex; flex-direction: column; gap: 16px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-header h2 { font-size: 18px; }
.filters { display: flex; gap: 8px; align-items: center; }
.filters select, .filters input {
  padding: 6px 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px;
}
.filters button { padding: 6px 12px; border: 1px solid #ddd; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12px; }
.filters .hint { font-size: 11px; color: #aaa; }

.cat-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cat-bar .cat-title { font-size: 12px; color: #888; }
.cat-chip {
  padding: 4px 12px; border-radius: 14px; border: 1px solid #e4e6ef;
  background: #fff; color: var(--cat, #666); font-size: 12px; cursor: pointer;
}
.cat-chip:hover { border-color: #ccc; }
.cat-chip.active { background: var(--cat, #165dff); border-color: var(--cat, #165dff); color: #fff; }
.cat-tag {
  font-size: 11px; padding: 1px 6px; border: 1px solid; border-radius: 8px;
}

.stats-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.stat-card {
  background: #fff; border: 1px solid #eee; border-radius: 8px; padding: 14px; text-align: center;
}
.stat-card .num { font-size: 24px; font-weight: 700; color: #165dff; }
.stat-card.user .num { color: #0bae6e; }
.stat-card.admin .num { color: #e86a17; }
.stat-card.msg .num { color: #6c5ce7; }
.stat-card .label { font-size: 12px; color: #888; margin-top: 4px; }

.layout { flex: 1; display: grid; grid-template-columns: 320px 1fr; gap: 16px; min-height: 0; }
.session-list {
  background: #fff; border: 1px solid #eee; border-radius: 8px;
  overflow-y: auto; padding: 8px;
}
.session-item {
  padding: 10px 12px; border-radius: 6px; cursor: pointer; margin-bottom: 4px;
  border: 1px solid transparent;
}
.session-item:hover { background: #f8f9fc; }
.session-item.active { background: #eef3ff; border-color: #165dff; }
.session-item .title { font-size: 13px; font-weight: 500; color: #222; margin-bottom: 4px; }
.session-item .meta { display: flex; gap: 6px; align-items: center; font-size: 11px; color: #999; flex-wrap: wrap; }
.source-tag { padding: 1px 6px; border-radius: 8px; font-size: 11px; }
.source-tag.user { background: #e8f7ee; color: #0bae6e; }
.source-tag.admin { background: #fef1e6; color: #e86a17; }
.session-item .count { font-size: 11px; color: #aaa; margin-top: 2px; }

.chat-area {
  background: #fff; border: 1px solid #eee; border-radius: 8px;
  overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 14px;
}
.chat-area.empty-main { justify-content: center; align-items: center; }
.chat-line { display: flex; flex-direction: column; }
.chat-line.user { align-items: flex-end; }
.chat-line.user .bubble { background: #165dff; color: #fff; }
.chat-line.user .time { text-align: right; }
.chat-line.assistant .bubble { background: #f0f2f7; }
.bubble {
  max-width: 80%; padding: 10px 14px; border-radius: 12px;
  font-size: 14px; line-height: 1.6;
}
.bubble pre { white-space: pre-wrap; word-break: break-word; font-family: inherit; }
.chat-line .time { font-size: 11px; color: #aaa; margin-top: 4px; }
.sources-inline { font-size: 11px; color: #165dff; margin-top: 4px; }
.empty { text-align: center; color: #aaa; padding: 30px; font-size: 13px; }
</style>
