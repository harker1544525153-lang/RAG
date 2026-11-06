<!-- 登录 / 注册：手机号 + 密码 + 图形验证码 -->
<script setup>
import { reactive, ref, onMounted } from 'vue'
import { api, setToken, USER_KEY } from '../api'

const emit = defineEmits(['success'])

const mode = ref('login')            // login / register
const loading = ref(false)
const error = ref('')
const captchaSvg = ref('')
const captchaId = ref('')
const phoneCheck = ref({ status: '', message: '' })   // '' / ok / error

const form = reactive({ phone: '', password: '', nickname: '', code: '' })

const PHONE_RE = /^1[3-9]\d{9}$/

let phoneCheckSeq = 0

async function loadCaptcha() {
  try {
    const data = await api.authCaptcha()
    captchaId.value = data.captcha_id
    captchaSvg.value = data.svg
  } catch {
    error.value = '验证码加载失败，请检查后端服务是否已启动'
  }
}

// 手机号失焦预检：格式 + 是否已注册（注册/登录给出不同提示）
async function onPhoneBlur() {
  const phone = form.phone.trim()
  if (!phone) {
    phoneCheck.value = { status: '', message: '' }
    return
  }
  if (!PHONE_RE.test(phone)) {
    phoneCheck.value = { status: 'error', message: '请输入正确的 11 位手机号' }
    return
  }
  const seq = ++phoneCheckSeq
  try {
    const data = await api.checkPhone(phone)
    if (seq !== phoneCheckSeq) return
    if (mode.value === 'register' && data.exists) {
      phoneCheck.value = { status: 'error', message: '该手机号已注册，请直接登录' }
    } else if (mode.value === 'login' && !data.exists) {
      phoneCheck.value = { status: 'error', message: '该手机号尚未注册，请先注册新账号' }
    } else {
      phoneCheck.value = {
        status: mode.value === 'register' ? 'ok' : '',
        message: mode.value === 'register' ? '该手机号可以注册' : ''
      }
    }
  } catch {
    if (seq === phoneCheckSeq) phoneCheck.value = { status: '', message: '' }
  }
}

function switchMode(next) {
  if (mode.value === next) return
  mode.value = next
  error.value = ''
  form.code = ''
  phoneCheck.value = { status: '', message: '' }
  loadCaptcha()
}

async function submit() {
  error.value = ''
  const phone = form.phone.trim()
  if (!phone) {
    error.value = '请输入手机号'
    return
  }
  if (!PHONE_RE.test(phone)) {
    error.value = '请输入正确的 11 位手机号'
    return
  }
  if (!form.password) {
    error.value = '请输入密码'
    return
  }
  if (form.password.length < 6 || form.password.length > 32) {
    error.value = '密码长度需为 6~32 位'
    return
  }
  if (mode.value === 'register' && /\s/.test(form.password)) {
    error.value = '密码不能包含空格'
    return
  }
  if (!form.code.trim()) {
    error.value = '请输入图形验证码'
    return
  }

  loading.value = true
  try {
    const payload = {
      phone,
      password: form.password,
      captcha_id: captchaId.value,
      captcha_code: form.code.trim()
    }
    const data = mode.value === 'login'
      ? await api.login(payload)
      : await api.register({ ...payload, nickname: form.nickname.trim() })

    setToken(data.token)
    localStorage.setItem(USER_KEY, JSON.stringify(data.user))
    emit('success', data.user)
  } catch (e) {
    const msg = (e && e.message) ? e.message : '操作失败，请稍后重试'
    error.value = /failed to fetch|networkerror|load failed/i.test(msg)
      ? '无法连接服务器，请确认后端服务已启动'
      : msg
    // 账号状态类错误同步到手机号提示
    if (/已注册|尚未注册|未注册/.test(msg)) {
      phoneCheck.value = { status: 'error', message: msg }
    }
    form.code = ''
    loadCaptcha()          // 验证码一次性，失败后自动换一张
  } finally {
    loading.value = false
  }
}

onMounted(loadCaptcha)
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="brand">
        <div class="logo">G</div>
        <div>
          <div class="brand-name">GALA 智能知识平台</div>
          <div class="brand-sub">企业级 RAG 文档问答系统</div>
        </div>
      </div>

      <div class="tabs">
        <button :class="{ active: mode === 'login' }" @click="switchMode('login')">账号登录</button>
        <button :class="{ active: mode === 'register' }" @click="switchMode('register')">注册新账号</button>
      </div>

      <form @submit.prevent="submit">
        <div class="field">
          <label>手机号</label>
          <input
            v-model="form.phone"
            maxlength="11"
            placeholder="请输入 11 位手机号"
            autocomplete="tel"
            @blur="onPhoneBlur"
          />
          <div v-if="phoneCheck.message" class="hint" :class="phoneCheck.status">
            {{ phoneCheck.message }}
          </div>
        </div>

        <div class="field" v-if="mode === 'register'">
          <label>昵称<span class="opt">选填</span></label>
          <input v-model="form.nickname" maxlength="20" placeholder="用于界面显示，默认取手机号后四位" />
        </div>

        <div class="field">
          <label>密码</label>
          <input
            v-model="form.password"
            type="password"
            maxlength="32"
            :placeholder="mode === 'register' ? '6~32 位字符，不能含空格' : '请输入登录密码'"
            :autocomplete="mode === 'register' ? 'new-password' : 'current-password'"
          />
        </div>

        <div class="field">
          <label>图形验证码</label>
          <div class="captcha-row">
            <input
              v-model="form.code"
              class="code-input"
              maxlength="4"
              placeholder="输入图中字符"
              autocomplete="off"
            />
            <div
              class="captcha-img"
              title="看不清？点击换一张"
              @click="loadCaptcha"
            >
              <span v-if="!captchaSvg" class="loading">加载中…</span>
              <span v-else v-html="captchaSvg"></span>
            </div>
          </div>
        </div>

        <div class="error" v-if="error">{{ error }}</div>

        <button class="submit" type="submit" :disabled="loading">
          {{ loading ? '处理中…' : (mode === 'login' ? '登 录' : '注 册') }}
        </button>
      </form>

      <div class="foot">
        <template v-if="mode === 'login'">
          还没有账号？<a @click="switchMode('register')">立即注册</a>
        </template>
        <template v-else>
          已有账号？<a @click="switchMode('login')">返回登录</a>
        </template>
      </div>

      <div class="tips">
        注册后即可使用智能问答、文档管理、问答记录与设置；<br />
        管理员账号额外拥有「用户管理」与「对话统计」模块。
      </div>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background:
    radial-gradient(900px 500px at 15% 10%, #e6efff 0%, transparent 60%),
    radial-gradient(700px 500px at 90% 85%, #eaf4ff 0%, transparent 55%),
    linear-gradient(135deg, #f7f9fc, #eef3fb);
}

.auth-card {
  width: 100%;
  max-width: 400px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 12px 40px rgba(22, 93, 255, 0.1);
  padding: 28px 30px 22px;
}

.brand { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
.logo {
  width: 42px; height: 42px;
  background: linear-gradient(135deg, #165dff, #4a8cff);
  border-radius: 10px;
  color: #fff; font-weight: 700; font-size: 22px;
  display: flex; align-items: center; justify-content: center;
}
.brand-name { font-size: 16px; font-weight: 700; letter-spacing: 0.3px; }
.brand-sub { font-size: 11px; color: var(--text-3); margin-top: 2px; }

.tabs {
  display: flex;
  background: #f2f4f8;
  border-radius: 8px;
  padding: 3px;
  margin-bottom: 20px;
}
.tabs button {
  flex: 1;
  padding: 8px 0;
  background: transparent;
  color: var(--text-2);
  font-size: 13px;
  border-radius: 6px;
}
.tabs button.active {
  background: #fff;
  color: var(--primary);
  font-weight: 600;
  box-shadow: 0 1px 4px rgba(29, 33, 41, 0.08);
}

.field { margin-bottom: 14px; }
.field label {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--text-2); margin-bottom: 6px;
}
.field .opt { font-size: 11px; color: var(--text-3); }
.hint { font-size: 11px; line-height: 1.6; margin-top: 6px; color: var(--text-3); }
.hint.error { color: var(--danger); }
.hint.ok { color: #0e9f6e; }
.field input {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 10px 12px;
  font-size: 13px;
  outline: none;
  transition: all 0.15s;
}
.field input:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(22, 93, 255, 0.08);
}

.captcha-row { display: flex; gap: 10px; align-items: stretch; }
.code-input { flex: 1; }
.captcha-img {
  width: 140px; height: 44px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: #f5f8ff;
  overflow: hidden;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: border-color 0.15s;
}
.captcha-img:hover { border-color: var(--primary); }
.captcha-img :deep(svg) { display: block; width: 100%; height: 100%; }
.captcha-img .loading { font-size: 12px; color: var(--text-3); }

.error {
  background: #ffece8;
  color: var(--danger);
  font-size: 12px;
  padding: 8px 12px;
  border-radius: 6px;
  margin-bottom: 12px;
}

.submit {
  width: 100%;
  padding: 11px 0;
  background: var(--primary);
  color: #fff;
  font-size: 14px;
  font-weight: 500;
  border-radius: var(--radius);
  margin-top: 4px;
}
.submit:hover { background: var(--primary-hover); }
.submit:disabled { background: #94bfff; cursor: not-allowed; }

.foot {
  text-align: center;
  font-size: 12px;
  color: var(--text-3);
  margin-top: 16px;
}
.foot a { color: var(--primary); cursor: pointer; text-decoration: none; }
.foot a:hover { text-decoration: underline; }

.tips {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px dashed var(--border);
  font-size: 11px;
  line-height: 1.7;
  color: var(--text-3);
  text-align: center;
}
</style>
