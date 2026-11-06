<!-- 对话统计（仅管理员）：平台对话助手使用情况 -->
<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'

const days = ref(7)
const data = ref(null)
const categoryMap = ref({})
const loading = ref(false)
const error = ref('')

const totals = computed(() => data.value?.totals || {})
const trend = computed(() => data.value?.trend || [])

const maxTrend = computed(() => {
  let max = 0
  for (const t of trend.value) max = Math.max(max, t.sessions, t.questions)
  return Math.max(1, max)
})

const sourceRows = computed(() => {
  const rows = data.value?.by_source || []
  const total = rows.reduce((s, r) => s + r.count, 0) || 1
  return rows.map(r => ({
    ...r,
    label: r.source === 'user' ? '用户端' : r.source === 'admin' ? '管理端' : r.source,
    percent: Math.round(r.count / total * 100)
  }))
})

const categoryRows = computed(() => {
  const rows = data.value?.by_category || []
  const max = Math.max(1, ...rows.map(r => r.count))
  return rows.map(r => {
    const cat = categoryMap.value[r.category]
    return {
      ...r,
      label: cat?.label || r.category || '未分类',
      color: cat?.color || '#86909c',
      percent: Math.round(r.count / max * 100)
    }
  })
})

const topQuestions = computed(() => data.value?.top_questions || [])

function dayLabel(date, index) {
  const step = Math.ceil(trend.value.length / 10)
  return index % step === 0 || index === trend.value.length - 1 ? date.slice(5) : ''
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [stats, sessionStats] = await Promise.all([
      api.chatStats(days.value),
      api.sessionStats()
    ])
    data.value = stats
    const map = {}
    for (const c of sessionStats.categories || []) map[c.id] = c
    categoryMap.value = map
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function setDays(d) {
  if (days.value === d) return
  days.value = d
  load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2>对话统计</h2>
        <span class="page-sub">了解当前平台对话助手的使用情况</span>
      </div>
      <div class="filters">
        <button
          v-for="d in [7, 30, 90]"
          :key="d"
          class="period-btn"
          :class="{ active: days === d }"
          @click="setDays(d)"
        >近 {{ d }} 天</button>
        <button class="btn-ghost" @click="load">刷新</button>
      </div>
    </div>

    <div v-if="error" class="error-bar">{{ error }}</div>
    <div v-if="loading && !data" class="empty">统计加载中…</div>

    <template v-if="data">
      <div class="stat-grid">
        <div class="stat-card">
          <div class="num">{{ totals.sessions }}</div>
          <div class="label">会话总数</div>
          <div class="sub">用户端 {{ totals.user_sessions }} · 管理端 {{ totals.admin_sessions }}</div>
        </div>
        <div class="stat-card green">
          <div class="num">{{ totals.questions }}</div>
          <div class="label">提问总数</div>
          <div class="sub">共 {{ totals.messages }} 条消息</div>
        </div>
        <div class="stat-card orange">
          <div class="num">{{ totals.users }}</div>
          <div class="label">注册用户</div>
          <div class="sub">平台全部账号</div>
        </div>
        <div class="stat-card purple">
          <div class="num">{{ totals.active_users }}</div>
          <div class="label">活跃访客（近 {{ days }} 天）</div>
          <div class="sub">按用户端访客去重</div>
        </div>
      </div>

      <!-- 趋势 -->
      <div class="card panel">
        <div class="panel-head">
          <span>使用趋势</span>
          <div class="legend">
            <span class="dot blue"></span>会话
            <span class="dot green"></span>提问
          </div>
        </div>
        <div class="chart">
          <div v-for="(t, i) in trend" :key="t.date" class="chart-col">
            <div class="bars">
              <div class="bar blue" :style="{ height: (t.sessions / maxTrend * 100) + '%' }" :title="`会话 ${t.sessions}`"></div>
              <div class="bar green" :style="{ height: (t.questions / maxTrend * 100) + '%' }" :title="`提问 ${t.questions}`"></div>
            </div>
            <div class="col-label">{{ dayLabel(t.date, i) }}</div>
          </div>
        </div>
      </div>

      <div class="two-col">
        <!-- 来源分布 -->
        <div class="card panel">
          <div class="panel-head"><span>会话来源分布</span></div>
          <div v-if="!sourceRows.length" class="empty">暂无数据</div>
          <div class="bar-list" v-else>
            <div v-for="r in sourceRows" :key="r.source" class="bar-row">
              <span class="bar-label">{{ r.label }}</span>
              <div class="bar-track">
                <div class="bar-fill blue" :style="{ width: r.percent + '%' }"></div>
              </div>
              <span class="bar-count">{{ r.count }}</span>
            </div>
          </div>
        </div>

        <!-- 分类分布 -->
        <div class="card panel">
          <div class="panel-head"><span>问答分类分布（Top 10）</span></div>
          <div v-if="!categoryRows.length" class="empty">暂无数据</div>
          <div class="bar-list" v-else>
            <div v-for="r in categoryRows" :key="r.category" class="bar-row">
              <span class="bar-label">{{ r.label }}</span>
              <div class="bar-track">
                <div class="bar-fill" :style="{ width: r.percent + '%', background: r.color }"></div>
              </div>
              <span class="bar-count">{{ r.count }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 热门问题 -->
      <div class="card panel">
        <div class="panel-head">
          <span>热门提问（近 {{ days }} 天重复次数 Top 8）</span>
        </div>
        <div v-if="!topQuestions.length" class="empty">暂无数据</div>
        <div class="question-list" v-else>
          <div v-for="(q, i) in topQuestions" :key="i" class="question-row">
            <span class="rank" :class="{ top: i < 3 }">{{ i + 1 }}</span>
            <span class="q-text">{{ q.question }}</span>
            <span class="q-count">{{ q.count }} 次</span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page {
  padding: 20px; height: 100%; overflow-y: auto;
  display: flex; flex-direction: column; gap: 14px;
}
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.page-header h2 { font-size: 18px; }
.filters { display: flex; gap: 8px; align-items: center; }
.period-btn {
  padding: 6px 14px; background: #fff; color: var(--text-2);
  border: 1px solid var(--border); font-size: 12px;
}
.period-btn.active { background: var(--primary-light); color: var(--primary); border-color: #b3ccff; font-weight: 600; }

.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.stat-card { background: #fff; border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; }
.stat-card .num { font-size: 26px; font-weight: 700; color: var(--primary); }
.stat-card.green .num { color: #0bae6e; }
.stat-card.orange .num { color: #e86a17; }
.stat-card.purple .num { color: #6c5ce7; }
.stat-card .label { font-size: 13px; color: var(--text-2); margin-top: 4px; }
.stat-card .sub { font-size: 11px; color: var(--text-3); margin-top: 4px; }

.panel { padding: 16px 18px; }
.panel-head {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 13px; font-weight: 600; margin-bottom: 14px;
}
.legend { display: flex; align-items: center; gap: 12px; font-size: 11px; color: var(--text-3); font-weight: 400; }
.dot { width: 8px; height: 8px; border-radius: 2px; display: inline-block; margin-right: 4px; }
.dot.blue, .bar-fill.blue, .bar.blue { background: #165dff; }
.dot.green, .bar.green { background: #0bae6e; }

.chart { display: flex; align-items: flex-end; gap: 6px; min-height: 180px; overflow-x: auto; padding-bottom: 4px; }
.chart-col { flex: 1; min-width: 18px; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; }
.bars { display: flex; gap: 2px; align-items: flex-end; width: 100%; height: 150px; }
.bar { flex: 1; min-height: 2px; border-radius: 2px 2px 0 0; transition: height 0.3s; }
.col-label { font-size: 10px; color: var(--text-3); margin-top: 6px; white-space: nowrap; height: 14px; }

.two-col { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
.bar-list { display: flex; flex-direction: column; gap: 10px; }
.bar-row { display: grid; grid-template-columns: 90px 1fr 44px; gap: 10px; align-items: center; }
.bar-label { font-size: 12px; color: var(--text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bar-track { height: 8px; background: #f1f3f8; border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }
.bar-count { font-size: 12px; color: var(--text-3); text-align: right; }

.question-list { display: flex; flex-direction: column; }
.question-row {
  display: grid; grid-template-columns: 28px 1fr 60px; gap: 10px; align-items: center;
  padding: 9px 0; border-bottom: 1px solid #f2f4f8;
}
.rank {
  width: 20px; height: 20px; border-radius: 4px;
  background: #f1f3f8; color: var(--text-3);
  font-size: 11px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.rank.top { background: var(--primary); color: #fff; }
.q-text { font-size: 13px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.q-count { font-size: 12px; color: var(--text-3); text-align: right; }

.empty { text-align: center; color: var(--text-3); padding: 24px; font-size: 13px; }
.error-bar { padding: 8px 12px; background: #ffece8; color: var(--danger); font-size: 12px; border-radius: 6px; }
</style>
