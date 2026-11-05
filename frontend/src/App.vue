<!-- 管理端骨架：左侧导航 + 内容区。页面在 views/ 下，主题变量在 style.css，改完需 npm run build -->
<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import DocumentsView from './views/DocumentsView.vue'
import SessionsView from './views/SessionsView.vue'
import SettingsView from './views/SettingsView.vue'

const tabs = [
  { key: 'assistant', label: '智能问答助手', paths: ['M5 8h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z', 'M9 13h.01', 'M15 13h.01'] },
  { key: 'documents', label: '文档管理', paths: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M14 2v6h6', 'M16 13H8', 'M16 17H8', 'M10 9H8'] },
  { key: 'sessions', label: '问答记录', paths: ['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'] },
  { key: 'settings', label: '设置', paths: ['M4 21v-7', 'M4 10V3', 'M12 21v-9', 'M12 8V3', 'M20 21v-5', 'M20 12V3', 'M1 14h6', 'M9 8h6', 'M17 16h6'] }
]

const queryTab = new URLSearchParams(window.location.search).get('tab')
const activeTab = ref(tabs.some(t => t.key === queryTab) ? queryTab : 'documents')
const health = ref(null)
const docCount = computed(() => health.value?.documents ?? 0)
const chunkCount = computed(() => health.value?.chunks ?? 0)

async function refreshHealth() {
  try { health.value = await fetch('/api/health').then(r => r.json()) } catch {}
}

onMounted(() => {
  refreshHealth()
  window.addEventListener('rag:refresh-health', refreshHealth)
})
onUnmounted(() => window.removeEventListener('rag:refresh-health', refreshHealth))
</script>

<template>
  <div class="layout">
    <!-- 左侧导航 -->
    <aside class="sidebar">
      <div class="brand">
        <div class="logo">G</div>
        <div>
          <div class="brand-name">GALA 管理后台</div>
          <div class="brand-sub">智能问答助手 · 知识库管理</div>
        </div>
      </div>

      <nav class="menu">
        <button
          v-for="t in tabs"
          :key="t.key"
          class="menu-item"
          :class="{ active: activeTab === t.key }"
          @click="activeTab = t.key"
        >
          <svg class="menu-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path v-for="(d, i) in t.paths" :key="i" :d="d" />
          </svg>
          <span class="menu-label">{{ t.label }}</span>
          <span v-if="t.key === 'documents' && docCount" class="menu-badge">{{ docCount }}</span>
        </button>
      </nav>

      <div class="side-status">
        <div class="status-row">
          <span class="dot" :class="{ offline: !health }"></span>
          {{ health ? '服务正常' : '连接中…' }}
        </div>
        <div class="status-meta" v-if="health">{{ health.database }} · {{ health.vector_backend }}</div>
        <div class="status-meta" v-if="health">{{ docCount }} 文档 · {{ chunkCount }} 切片</div>
        <div class="hint" v-if="health">
          用户端入口：<a href="/" target="_blank">点击访问 →</a>
        </div>
      </div>
    </aside>

    <!-- 内容区 -->
    <main class="main">
      <!-- 管理端智能问答：复用用户端页面，加 ?source=admin 让会话记为管理端会话 -->
      <iframe
        v-show="activeTab === 'assistant'"
        class="assistant-frame"
        src="/user/index.html?source=admin"
        title="智能问答助手"
      ></iframe>
      <DocumentsView v-show="activeTab === 'documents'" />
      <SessionsView v-show="activeTab === 'sessions'" />
      <SettingsView v-show="activeTab === 'settings'" />
    </main>
  </div>
</template>

<style scoped>
.layout { display: flex; height: 100vh; }

.sidebar {
  width: 212px;
  flex-shrink: 0;
  background: var(--panel);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 18px 12px 14px;
}
.brand { display: flex; align-items: center; gap: 10px; padding: 0 6px 18px; }
.logo {
  width: 34px; height: 34px;
  background: linear-gradient(135deg, #165dff, #4a8cff);
  border-radius: 8px;
  color: #fff; font-weight: 700; font-size: 18px;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.brand-name { font-size: 14px; font-weight: 600; white-space: nowrap; }
.brand-sub { font-size: 11px; color: var(--text-3); }

.menu { display: flex; flex-direction: column; gap: 4px; }
.menu-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px;
  background: transparent;
  color: var(--text-2);
  text-align: left;
  border-radius: var(--radius);
  font-size: 14px;
}
.menu-item:hover { background: #f2f5fa; }
.menu-item.active { background: var(--primary-light); color: var(--primary); font-weight: 600; }
.menu-icon { width: 18px; height: 18px; flex-shrink: 0; }
.menu-label { flex: 1; }
.menu-badge {
  background: var(--primary); color: #fff;
  font-size: 11px; padding: 0 7px; border-radius: 8px; line-height: 17px;
}

.side-status {
  margin-top: auto;
  border-top: 1px solid var(--border);
  padding: 14px 6px 0;
  display: flex; flex-direction: column; gap: 4px;
}
.status-row { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-2); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--success); }
.dot.offline { background: var(--text-3); }
.status-meta { font-size: 11px; color: var(--text-3); }
.hint { font-size: 11px; color: var(--text-3); margin-top: 6px; }
.hint a { color: var(--primary); text-decoration: none; }
.hint a:hover { text-decoration: underline; }

.main { flex: 1; min-width: 0; overflow: hidden; }
.assistant-frame { width: 100%; height: 100%; border: 0; display: block; background: #f5f6fa; }
</style>
