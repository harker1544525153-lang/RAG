<!-- 用户管理（仅管理员）：查看基本信息、编辑、改角色、禁用、重置密码、删除、登录记录 -->
<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { api, USER_KEY } from '../api'

const PAGE_SIZE = 20

const me = ref(JSON.parse(localStorage.getItem(USER_KEY) || '{}'))
const users = ref([])
const total = ref(0)
const page = ref(1)
const keyword = ref('')
const roleFilter = ref('')
const statusFilter = ref('')
const loading = ref(false)
const error = ref('')

const dialog = ref('')          // '' | 'edit' | 'password' | 'logs'
const current = ref(null)
const actionError = ref('')
const saving = ref(false)
const editForm = reactive({ nickname: '', role: 'user' })
const newPassword = ref('')
const logs = ref([])

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const isSelf = u => u && u.id === me.value.id

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.listUsers({
      keyword: keyword.value.trim(),
      role: roleFilter.value,
      disabled: statusFilter.value,
      page: page.value,
      page_size: PAGE_SIZE
    })
    users.value = data.items
    total.value = data.total
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

function resetFilters() {
  keyword.value = ''
  roleFilter.value = ''
  statusFilter.value = ''
  page.value = 1
  load()
}

function goto(delta) {
  const next = page.value + delta
  if (next < 1 || next > pageCount.value) return
  page.value = next
  load()
}

function openDialog(type, u) {
  current.value = u
  actionError.value = ''
  if (type === 'edit') {
    editForm.nickname = u.nickname || ''
    editForm.role = u.role
  }
  if (type === 'password') newPassword.value = ''
  if (type === 'logs') {
    logs.value = []
    api.getUser(u.id).then(d => { logs.value = d.login_logs || [] }).catch(e => { actionError.value = e.message })
  }
  dialog.value = type
}

function closeDialog() {
  dialog.value = ''
  current.value = null
}

async function saveEdit() {
  saving.value = true
  actionError.value = ''
  try {
    await api.updateUser(current.value.id, {
      nickname: editForm.nickname.trim(),
      role: editForm.role
    })
    closeDialog()
    await load()
  } catch (e) {
    actionError.value = e.message
  } finally {
    saving.value = false
  }
}

async function savePassword() {
  if (newPassword.value.length < 6 || newPassword.value.length > 32) {
    actionError.value = '密码长度需为 6~32 位'
    return
  }
  saving.value = true
  actionError.value = ''
  try {
    await api.resetUserPassword(current.value.id, newPassword.value)
    closeDialog()
    alert('密码已重置，用户需使用新密码重新登录')
  } catch (e) {
    actionError.value = e.message
  } finally {
    saving.value = false
  }
}

async function toggleDisabled(u) {
  try {
    await api.updateUser(u.id, { disabled: !u.disabled })
    await load()
  } catch (e) {
    alert(e.message)
  }
}

async function removeUser(u) {
  if (!window.confirm(`确定删除账号「${u.nickname || u.phone}」吗？该操作不可恢复。`)) return
  try {
    await api.deleteUser(u.id)
    await load()
  } catch (e) {
    alert(e.message)
  }
}

async function copyWidgetLink(u) {
  try {
    const data = await api.widgetLink(u.id)
    const url = window.location.origin + data.url
    try {
      await navigator.clipboard.writeText(url)
      alert(`用户端问答链接已复制（该账号的文档与模型配置，免登录）：\n${url}`)
    } catch {
      window.prompt('用户端问答链接（请手动复制）：', url)
    }
  } catch (e) {
    alert(e.message)
  }
}

function roleLabel(r) {
  return r === 'admin' ? '管理员' : '普通用户'
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2>用户管理</h2>
        <span class="page-sub">共 {{ total }} 个账号 · 当前登录：{{ me.nickname || me.phone }}</span>
      </div>
      <div class="filters">
        <input v-model="keyword" placeholder="搜索手机号 / 昵称" @keyup.enter="search" />
        <select v-model="roleFilter" @change="search">
          <option value="">全部角色</option>
          <option value="user">普通用户</option>
          <option value="admin">管理员</option>
        </select>
        <select v-model="statusFilter" @change="search">
          <option value="">全部状态</option>
          <option value="0">正常</option>
          <option value="1">已禁用</option>
        </select>
        <button class="btn-primary" @click="search">搜索</button>
        <button class="btn-ghost" @click="resetFilters">重置</button>
      </div>
    </div>

    <div class="card table-card">
      <div v-if="error" class="error-bar">{{ error }}</div>

      <div class="table-wrap">
        <table>
        <thead>
          <tr>
            <th>用户</th>
            <th>角色</th>
            <th>状态</th>
            <th>登录次数</th>
            <th>最近登录</th>
            <th>注册时间</th>
            <th class="ops">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td>
              <div class="user-cell">
                <div class="avatar">{{ (u.nickname || u.phone).slice(0, 1) }}</div>
                <div>
                  <div class="u-name">
                    {{ u.nickname || u.phone }}
                    <span v-if="isSelf(u)" class="self-tag">当前</span>
                  </div>
                  <div class="u-phone">{{ u.phone }}</div>
                </div>
              </div>
            </td>
            <td>
              <span class="role-tag" :class="u.role">{{ roleLabel(u.role) }}</span>
            </td>
            <td>
              <span class="tag" :class="u.disabled ? 'tag-danger' : 'tag-success'">
                {{ u.disabled ? '已禁用' : '正常' }}
              </span>
            </td>
            <td>{{ u.login_count }}</td>
            <td class="muted">{{ u.last_login_at || '—' }}</td>
            <td class="muted">{{ u.created_at || '—' }}</td>
            <td class="ops">
              <button class="mini" @click="openDialog('edit', u)">编辑</button>
              <button class="mini" @click="openDialog('password', u)">重置密码</button>
              <button class="mini" @click="openDialog('logs', u)">登录记录</button>
              <button class="mini" @click="copyWidgetLink(u)">用户端链接</button>
              <button class="mini" @click="toggleDisabled(u)" :disabled="isSelf(u)">
                {{ u.disabled ? '启用' : '禁用' }}
              </button>
              <button class="mini danger" @click="removeUser(u)" :disabled="isSelf(u)">删除</button>
            </td>
          </tr>
          <tr v-if="!users.length && !loading">
            <td colspan="7" class="empty">没有匹配的用户</td>
          </tr>
        </tbody>
      </table>
      </div>

      <div class="pager">
        <span class="pager-info">第 {{ page }} / {{ pageCount }} 页 · 共 {{ total }} 条</span>
        <div class="pager-btns">
          <button class="mini" :disabled="page <= 1" @click="goto(-1)">上一页</button>
          <button class="mini" :disabled="page >= pageCount" @click="goto(1)">下一页</button>
        </div>
      </div>
    </div>

    <!-- 编辑资料 -->
    <div v-if="dialog === 'edit'" class="dialog-mask" @click.self="closeDialog">
      <div class="dialog">
        <div class="dialog-head">编辑用户信息</div>
        <div class="dialog-body">
          <div class="field">
            <label>手机号</label>
            <input :value="current.phone" disabled />
          </div>
          <div class="field">
            <label>昵称</label>
            <input v-model="editForm.nickname" maxlength="20" placeholder="界面显示名称" />
          </div>
          <div class="field">
            <label>角色</label>
            <select v-model="editForm.role" :disabled="isSelf(current)">
              <option value="user">普通用户（问答 / 文档 / 问答记录 / 设置）</option>
              <option value="admin">管理员（额外用户管理 / 对话统计）</option>
            </select>
            <div class="field-hint" v-if="isSelf(current)">当前登录账号不能修改自己的角色</div>
          </div>
          <div class="error-bar" v-if="actionError">{{ actionError }}</div>
        </div>
        <div class="dialog-foot">
          <button class="btn-ghost" @click="closeDialog">取消</button>
          <button class="btn-primary" :disabled="saving" @click="saveEdit">保存</button>
        </div>
      </div>
    </div>

    <!-- 重置密码 -->
    <div v-if="dialog === 'password'" class="dialog-mask" @click.self="closeDialog">
      <div class="dialog">
        <div class="dialog-head">重置密码</div>
        <div class="dialog-body">
          <div class="field-hint">正在为「{{ current.nickname || current.phone }}」设置新密码</div>
          <div class="field">
            <label>新密码</label>
            <input v-model="newPassword" type="password" maxlength="32" placeholder="6~32 位字符" />
          </div>
          <div class="error-bar" v-if="actionError">{{ actionError }}</div>
        </div>
        <div class="dialog-foot">
          <button class="btn-ghost" @click="closeDialog">取消</button>
          <button class="btn-primary" :disabled="saving" @click="savePassword">确认重置</button>
        </div>
      </div>
    </div>

    <!-- 登录记录 -->
    <div v-if="dialog === 'logs'" class="dialog-mask" @click.self="closeDialog">
      <div class="dialog wide">
        <div class="dialog-head">登录记录 · {{ current.nickname || current.phone }}</div>
        <div class="dialog-body">
          <div v-if="actionError" class="error-bar">{{ actionError }}</div>
          <div v-else-if="!logs.length" class="empty">暂无登录记录</div>
          <div class="log-list" v-else>
            <div v-for="item in logs" :key="item.id" class="log-row">
              <span class="tag" :class="item.success ? 'tag-success' : 'tag-danger'">
                {{ item.success ? '成功' : '失败' }}
              </span>
              <span class="log-reason">{{ item.reason }}</span>
              <span class="log-time">{{ item.created_at }}</span>
              <span class="log-ip">{{ item.ip || '—' }}</span>
            </div>
          </div>
        </div>
        <div class="dialog-foot">
          <button class="btn-ghost" @click="closeDialog">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { padding: 20px; height: 100%; display: flex; flex-direction: column; gap: 14px; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.page-header h2 { font-size: 18px; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.filters input, .filters select { padding: 6px 10px; font-size: 13px; }
.filters button { padding: 6px 14px; font-size: 13px; }

.table-card { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-height: 0; }
.table-wrap { flex: 1; overflow-y: auto; min-height: 0; }
table { border-collapse: collapse; width: 100%; }
thead th {
  position: sticky; top: 0; z-index: 1;
  background: #f7f9fc; color: var(--text-2);
  font-size: 12px; font-weight: 600; text-align: left;
  padding: 11px 14px;
  border-bottom: 1px solid var(--border);
}
tbody td {
  padding: 11px 14px;
  font-size: 13px;
  border-bottom: 1px solid #f2f4f8;
  vertical-align: middle;
}
tbody tr:hover { background: #fafbfe; }
.muted { color: var(--text-3); font-size: 12px; white-space: nowrap; }

.user-cell { display: flex; align-items: center; gap: 9px; }
.avatar {
  width: 30px; height: 30px; flex-shrink: 0;
  border-radius: 50%;
  background: linear-gradient(135deg, #165dff, #4a8cff);
  color: #fff; font-size: 13px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.u-name { font-size: 13px; font-weight: 500; }
.u-phone { font-size: 11px; color: var(--text-3); margin-top: 2px; }
.self-tag {
  font-size: 10px; color: var(--primary); background: var(--primary-light);
  padding: 1px 5px; border-radius: 4px; margin-left: 5px;
}
.role-tag {
  display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px;
}
.role-tag.admin { background: #fff1e8; color: #e86a17; }
.role-tag.user { background: var(--primary-light); color: var(--primary); }

.ops { white-space: nowrap; }
.mini {
  padding: 4px 9px; margin-right: 5px;
  background: #f4f6fb; color: var(--text-2);
  font-size: 12px; border-radius: 5px;
}
.mini:hover:not(:disabled) { background: var(--primary-light); color: var(--primary); }
.mini.danger:hover:not(:disabled) { background: #ffece8; color: var(--danger); }
.mini:disabled { opacity: 0.4; cursor: not-allowed; }

.empty { text-align: center; color: var(--text-3); padding: 40px; font-size: 13px; }
.error-bar {
  margin: 12px 14px; padding: 8px 12px;
  background: #ffece8; color: var(--danger);
  font-size: 12px; border-radius: 6px;
}

.pager {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 14px; border-top: 1px solid var(--border);
}
.pager-info { font-size: 12px; color: var(--text-3); }
.pager-btns { display: flex; gap: 6px; }

.dialog-mask {
  position: fixed; inset: 0; z-index: 50;
  background: rgba(29, 33, 41, 0.45);
  display: flex; align-items: center; justify-content: center;
}
.dialog {
  width: 460px; max-width: calc(100vw - 40px);
  background: #fff; border-radius: 12px;
  box-shadow: 0 16px 48px rgba(29, 33, 41, 0.2);
  overflow: hidden;
}
.dialog.wide { width: 620px; }
.dialog-head {
  padding: 18px 22px 0;
  font-size: 15px; font-weight: 600;
}
.dialog-body { padding: 16px 22px; max-height: 60vh; overflow-y: auto; }
.dialog-foot {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: 14px 22px 18px;
  border-top: 1px solid var(--border);
}
.field { margin-bottom: 14px; }
.field label { display: block; font-size: 12px; color: var(--text-2); margin-bottom: 6px; }
.field input, .field select { width: 100%; }
.field input:disabled, .field select:disabled { background: #f7f8fa; color: var(--text-3); }
.field-hint { font-size: 11px; color: var(--text-3); margin-bottom: 10px; }

.log-list { display: flex; flex-direction: column; gap: 2px; }
.log-row {
  display: grid; grid-template-columns: 56px 1fr 140px 110px;
  gap: 8px; align-items: center;
  padding: 9px 10px; font-size: 12px;
  border-bottom: 1px solid #f2f4f8;
}
.log-reason { color: var(--text-2); }
.log-time { color: var(--text-3); white-space: nowrap; }
.log-ip { color: var(--text-3); text-align: right; }
</style>
