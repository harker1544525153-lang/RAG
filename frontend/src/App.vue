<!-- 管理端骨架：登录门禁 + 左侧导航 + 内容区。页面在 views/ 下，主题变量在 style.css，改完需 npm run build -->
<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { api, getToken, clearAuth, USER_KEY } from './api'
import AuthView from './views/AuthView.vue'
import DocumentsView from './views/DocumentsView.vue'
import SessionsView from './views/SessionsView.vue'
import SettingsView from './views/SettingsView.vue'
import UsersView from './views/UsersView.vue'
import StatsView from './views/StatsView.vue'

const tabs = [
  { key: 'assistant', label: '智能问答助手', paths: ['M5 8h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z', 'M9 13h.01', 'M15 13h.01'] },
  { key: 'documents', label: '文档管理', paths: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M14 2v6h6', 'M16 13H8', 'M16 17H8', 'M10 9H8'] },
  { key: 'sessions', label: '问答记录', paths: ['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'] },
  { key: 'users', label: '用户管理', adminOnly: true, paths: ['M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2', 'M9 7a4 4 0 1 0 0 8 4 4 0 0 0 0-8z', 'M23 21v-2a4 4 0 0 0-3-3.87', 'M16 3.13a4 4 0 0 1 0 7.75'] },
  { key: 'stats', label: '对话统计', adminOnly: true, paths: ['M12 20V10', 'M18 20V4', 'M6 20v-4'] },
  { key: 'settings', label: '设置', paths: ['M4 21v-7', 'M4 10V3', 'M12 21v-9', 'M12 8V3', 'M20 21v-5', 'M20 12V3', 'M1 14h6', 'M9 8h6', 'M17 16h6'] }
]

const user = ref(null)
const booting = ref(true)
const queryTab = new URLSearchParams(window.location.search).get('tab')
const activeTab = ref('documents')
const menuOpen = ref(false)
const health = ref(null)
// 文档/切片数按当前账号统计（health 是全站数据，不能用于侧边栏徽标）
const myStats = ref(null)
const docCount = computed(() => myStats.value?.total_documents ?? 0)
const chunkCount = computed(() => myStats.value?.total_chunks ?? 0)

async function refreshStats() {
  try { myStats.value = await api.documentStats() } catch { myStats.value = null }
}

const isAdmin = computed(() => user.value?.role === 'admin')
const visibleTabs = computed(() => tabs.filter(t => !t.adminOnly || isAdmin.value))
const currentLabel = computed(() => tabs.find(t => t.key === activeTab.value)?.label || '')

// 品牌信息（设置 → 信息分类）：按当前账号读取
const brand = ref(null)
const brandTitle = computed(() => brand.value?.admin_title || 'GALA 管理后台')
const brandSub = computed(() => brand.value?.admin_subtitle || '智能问答助手 · 知识库管理')
const brandLogo = computed(() => brand.value?.admin_logo || '')

async function refreshBrand() {
  try { brand.value = await api.brand() } catch {}
}

// 管理端内嵌用户端页面：把登录令牌透传过去，iframe 内问答与会话按当前账号隔离
const assistantSrc = computed(() => {
  // 必须依赖 user：computed 只有依赖变化才会重算；若只读 localStorage（非响应式），
  // 切换账号后 iframe 会一直沿用上一个账号的地址/令牌，导致显示成上一个账号的后台
  const uid = user.value?.id
  const token = getToken()
  const base = '/user/index.html?source=admin'
  return uid && token ? `${base}&token=${encodeURIComponent(token)}` : base
})

watch(visibleTabs, list => {
  if (!list.some(t => t.key === activeTab.value)) activeTab.value = 'documents'
})

function selectTab(key) {
  activeTab.value = key
  menuOpen.value = false
}

async function refreshHealth() {
  try { health.value = await fetch('/api/health').then(r => r.json()) } catch {}
}

function onLogin(u) {
  user.value = u
  if (queryTab && visibleTabs.value.some(t => t.key === queryTab)) activeTab.value = queryTab
  refreshHealth()
  refreshBrand()
  refreshStats()
}

async function boot() {
  if (!getToken()) { booting.value = false; return }
  try {
    const data = await api.me()
    localStorage.setItem(USER_KEY, JSON.stringify(data.user))
    onLogin(data.user)
  } catch {
    clearAuth()
  } finally {
    booting.value = false
  }
}

async function logout() {
  try { await api.logout() } catch {}
  clearAuth()
  user.value = null
  activeTab.value = 'documents'
}

function onUnauthorized() {
  user.value = null
}

// 同一浏览器多个标签页共享 localStorage：别的标签页登录/登出都会改写令牌，
// 本页内存里的身份随即过期（会出现「页面还显示管理员、操作却提示无权限」），
// 这里监听变化并重新校验身份。
function onStorage(e) {
  if (e.key !== 'gala_token') return
  if (!e.newValue) {
    user.value = null
    return
  }
  boot()
}

onMounted(() => {
  boot()
  refreshHealth()
  // 文档上传/删除/重建后会广播 rag:refresh-health，这里同时刷新账号自己的文档统计
  window.addEventListener('rag:refresh-health', refreshHealth)
  window.addEventListener('rag:refresh-health', refreshStats)
  window.addEventListener('gala:brand-updated', refreshBrand)
  window.addEventListener('gala:unauthorized', onUnauthorized)
  window.addEventListener('storage', onStorage)
})
onUnmounted(() => {
  window.removeEventListener('rag:refresh-health', refreshHealth)
  window.removeEventListener('rag:refresh-health', refreshStats)
  window.removeEventListener('gala:brand-updated', refreshBrand)
  window.removeEventListener('gala:unauthorized', onUnauthorized)
  window.removeEventListener('storage', onStorage)
})
</script>

<template>
  <!-- 未登录：登录 / 注册页 -->
  <AuthView v-if="!booting && !user" @success="onLogin" />

  <!-- 校验登录态中 -->
  <div v-else-if="booting" class="boot-page">正在校验登录状态…</div>

  <div class="layout" v-else>
    <!-- 左侧导航 -->
    <div v-if="menuOpen" class="sidebar-backdrop" @click="menuOpen = false"></div>
    <aside class="sidebar" :class="{ open: menuOpen }">
      <div class="brand">
        <div class="logo">
          <img v-if="brandLogo" :src="brandLogo" alt="logo" />
          <svg v-else viewBox="0 0 48 48" role="img" aria-label="GALA">
            <rect x="1" y="1" width="46" height="46" rx="12" fill="#165dff"/>
            <path d="M12 12h24a6 6 0 0 1 6 6v8a6 6 0 0 1-6 6h-13l-8 7v-7h-3a6 6 0 0 1-6-6v-8a6 6 0 0 1 6-6z" fill="#fff"/>
            <circle cx="17" cy="22" r="2.3" fill="#165dff"/>
            <circle cx="24" cy="22" r="2.3" fill="#165dff"/>
            <circle cx="31" cy="22" r="2.3" fill="#165dff"/>
          </svg>
        </div>
        <div>
          <div class="brand-name">{{ brandTitle }}</div>
          <div class="brand-sub">{{ brandSub }}</div>
        </div>
      </div>

      <nav class="menu">
        <button
          v-for="t in visibleTabs"
          :key="t.key"
          class="menu-item"
          :class="{ active: activeTab === t.key }"
          @click="selectTab(t.key)"
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

        <!-- 当前登录用户 -->
        <div class="side-user">
          <div class="avatar">{{ (user.nickname || user.phone).slice(0, 1) }}</div>
          <div class="user-info">
            <div class="user-name">{{ user.nickname || user.phone }}</div>
            <div class="user-meta">{{ user.phone }} · {{ isAdmin ? '管理员' : '普通用户' }}</div>
          </div>
          <button class="logout-btn" title="退出登录" @click="logout">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <path d="M16 17l5-5-5-5" />
              <path d="M21 12H9" />
            </svg>
          </button>
        </div>
      </div>
    </aside>

    <!-- 内容区 -->
    <main class="main">
      <!-- 移动端顶栏：汉堡菜单 + 当前页面标题 -->
      <div class="mobile-topbar">
        <button class="menu-toggle" aria-label="打开菜单" @click="menuOpen = true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <span class="mobile-title">{{ currentLabel }}</span>
      </div>

      <!-- 管理端智能问答：复用用户端页面，加 ?source=admin 让会话记为管理端会话 -->
      <div class="main-content">
        <iframe
          v-show="activeTab === 'assistant'"
          :key="user.id"
          class="assistant-frame"
          :src="assistantSrc"
          title="智能问答助手"
        ></iframe>
        <DocumentsView v-show="activeTab === 'documents'" />
        <SessionsView v-show="activeTab === 'sessions'" />
        <UsersView v-if="isAdmin" v-show="activeTab === 'users'" />
        <StatsView v-if="isAdmin" v-show="activeTab === 'stats'" />
        <SettingsView v-show="activeTab === 'settings'" />
      </div>
    </main>
  </div>
</template>

<style scoped>
.boot-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-3);
  font-size: 13px;
}

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
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  overflow: hidden;
}
.logo svg { width: 100%; height: 100%; display: block; }
.logo img { width: 100%; height: 100%; object-fit: contain; display: block; }
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

.side-user {
  display: flex; align-items: center; gap: 8px;
  margin-top: 12px; padding: 10px 8px;
  background: #f7f9fc;
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.avatar {
  width: 28px; height: 28px; flex-shrink: 0;
  border-radius: 50%;
  background: linear-gradient(135deg, #165dff, #4a8cff);
  color: #fff; font-size: 13px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.user-info { flex: 1; min-width: 0; }
.user-name {
  font-size: 12px; font-weight: 600; color: var(--text);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.user-meta { font-size: 10px; color: var(--text-3); margin-top: 2px; }
.logout-btn {
  background: transparent; color: var(--text-3);
  padding: 4px; border-radius: 4px;
  display: flex; align-items: center;
}
.logout-btn:hover { color: var(--danger); background: #ffece8; }
.logout-btn svg { width: 16px; height: 16px; }

.main { flex: 1; min-width: 0; overflow: hidden; display: flex; flex-direction: column; }
.mobile-topbar { display: none; }
.main-content { flex: 1; min-height: 0; position: relative; }
.assistant-frame { width: 100%; height: 100%; border: 0; display: block; background: #f5f6fa; }
</style>
