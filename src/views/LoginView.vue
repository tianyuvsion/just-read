<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowRight, BookOpen, Check, Eye, EyeOff, LockKeyhole, LogIn, ShieldCheck, Sparkles, UserRound, X } from 'lucide-vue-next'
import { auth, login, safeReturnPath } from '../services/auth'

const route = useRoute()
const username = ref('')
const password = ref('')
const showPassword = ref(false)
const busy = ref(false)
const error = ref('')
const usernameInput = ref<HTMLInputElement>()
const guide = ref<HTMLDialogElement>()
// Recreate account-scoped stores if a session became available in another tab.
function retrySession() { location.reload() }
watch([() => auth.guideOpen, guide], ([open]) => {
  if (open && guide.value && !guide.value.open) guide.value.showModal()
  else if (!open && guide.value?.open) guide.value.close()
}, { flush: 'post' })
const destination = computed(() => safeReturnPath(route.query.redirect ?? auth.redirect))
const guideTitle = computed(() => auth.guideReason === 'expired' ? '登录已失效' : auth.guideReason === 'changed' ? '登录状态已更新' : '登录后，继续探索')
async function dismissGuide() { auth.guideOpen = false; await nextTick(); usernameInput.value?.focus() }
async function submit() {
  if (busy.value) return
  error.value = ''
  if (!username.value.trim() || !password.value) { error.value = '请输入账号和密码。'; return }
  busy.value = true
  try { await login(username.value, password.value, destination.value) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '登录失败，请重试。'; password.value = '' }
  finally { busy.value = false }
}
</script>

<template>
  <main class="login-page">
    <header class="login-header"><RouterLink to="/login" class="brand" aria-label="Just Read 登录"><span class="brand-mark"><BookOpen :size="22" /></span><span>just read<span class="brand-dot">.</span></span></RouterLink><span class="login-header-note">少一点信息，更多一点洞察</span></header>
    <div class="login-content">
      <section class="login-story" aria-label="Just Read 工作空间">
        <div class="login-eyebrow"><span></span>你的知识工作空间</div>
        <h1>让每一个好问题，<br>都有更清晰的<span>答案。</span></h1>
        <p class="login-intro">从资料到洞察，从对话到报告。<br>登录 Just Read，继续你的探索。</p>
        <div class="login-preview" aria-hidden="true">
          <div class="preview-title"><span><BookOpen :size="17" /> 一份报告，更多理解</span><span class="preview-badge">READY TO READ</span></div>
          <div class="preview-body"><div class="preview-paper"><small>RESEARCH NOTES / 01</small><strong>把复杂的知识，<br>变成清晰的表达。</strong><div class="preview-lines"><i></i><i></i><i></i></div><div class="preview-chart"><span></span><span></span><span></span><span></span><span></span><span></span></div></div><div class="preview-notes"><span><Check :size="14" /> 关键观点，清晰呈现</span><span><Check :size="14" /> 对话记录，随时继续</span><div class="preview-note"><Sparkles :size="19" /><p>选一个喜欢的风格，<br>写下你的下一份报告。</p></div></div></div>
        </div>
      </section>
      <section class="login-card" aria-labelledby="login-title">
        <div class="login-card-icon"><LogIn :size="25" /></div>
        <h2 id="login-title">欢迎回来</h2><p class="login-subtitle">登录，进入你的工作空间</p>
        <form @submit.prevent="submit" :aria-busy="busy">
          <label for="login-username">账号</label><div class="login-field"><UserRound :size="18" /><input id="login-username" ref="usernameInput" v-model="username" name="username" autocomplete="username" maxlength="64" placeholder="请输入内部账号" :disabled="busy" required /></div>
          <label for="login-password">密码</label><div class="login-field"><LockKeyhole :size="18" /><input id="login-password" v-model="password" name="password" :type="showPassword ? 'text' : 'password'" autocomplete="current-password" maxlength="128" placeholder="请输入密码" :disabled="busy" required /><button type="button" class="password-toggle" :aria-label="showPassword ? '隐藏密码' : '显示密码'" :aria-pressed="showPassword" @click="showPassword = !showPassword"><EyeOff v-if="showPassword" :size="17" /><Eye v-else :size="17" /></button></div>
          <p v-if="error" class="login-error" role="alert">{{ error }}</p>
          <div v-if="auth.serviceError" class="login-service-error" role="status"><span>{{ auth.serviceError }}</span><button type="button" :disabled="auth.checking || busy" @click="retrySession">{{ auth.checking ? '正在检查…' : '重新检查' }}</button></div>
          <button class="login-submit" type="submit" :disabled="busy || auth.checking"><span>{{ busy ? '正在登录…' : '登录工作空间' }}</span><ArrowRight v-if="!busy" :size="18" /><span v-else class="login-spinner"></span></button>
        </form>
        <p class="login-account-help">内部账号由管理员提供。<br>如需开通账号或重置密码，请联系管理员。</p>
        <div class="login-private"><ShieldCheck :size="15" />登录后可使用 AI 对话、调研与报告功能</div>
      </section>
    </div>
    <footer class="login-footer"><span>让知识更易理解</span><span>JUST READ · BUILT FOR CURIOSITY</span></footer>
    <Teleport to="body"><dialog ref="guide" class="login-guide" aria-labelledby="login-guide-title" aria-describedby="login-guide-description" @cancel.prevent="dismissGuide"><button class="guide-close" type="button" aria-label="关闭登录引导" @click="dismissGuide"><X :size="19" /></button><div class="login-card-icon"><LockKeyhole :size="25" /></div><h2 id="login-guide-title">{{ guideTitle }}</h2><p id="login-guide-description">{{ auth.guideReason === 'expired' ? '请重新登录后继续操作，已保存的内容会保留。' : auth.guideReason === 'changed' ? '其他页面更新了登录状态，请重新确认后继续。' : 'AI 对话、调研和报告功能需要登录。使用内部账号登录后，即可继续访问。' }}</p><button class="login-submit" type="button" autofocus @click="dismissGuide">前往登录 <ArrowRight :size="17" /></button></dialog></Teleport>
  </main>
</template>

<style scoped>
.login-page{min-height:100dvh;background:radial-gradient(ellipse at 12% 42%,#e6f4ff 0,transparent 55%),#f8fafc;color:#1f2329;display:flex;flex-direction:column}.login-header{max-width:1280px;width:100%;margin:auto;padding:32px 48px;display:flex;align-items:center;justify-content:space-between}.login-header-note{font-size:12px;color:#7b8797}.login-content{width:min(1120px,100%);margin:auto;display:grid;grid-template-columns:1.25fr 1fr;gap:80px;align-items:center;padding:42px 32px 64px}.login-eyebrow{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:600;color:#1677ff;letter-spacing:2px}.login-eyebrow>span{height:6px;width:6px;border-radius:50%;background:#1677ff}.login-story h1{font-size:clamp(30px,3.3vw,44px);letter-spacing:-1.5px;line-height:1.5;margin:22px 0 18px;font-weight:750}.login-story h1>span{color:#1677ff}.login-intro{font-size:14px;line-height:1.9;color:#6e7a8b}.login-preview{margin-top:36px;background:#ffffffa6;border:1px solid #fff;border-radius:14px;padding:18px;box-shadow:0 16px 50px #1f5c9610}.preview-title{display:flex;justify-content:space-between;align-items:center;gap:8px;border-bottom:1px solid #e8eef5;padding-bottom:14px;font-size:12px;color:#58728e}.preview-title>span:first-child{display:flex;gap:8px;align-items:center}.preview-badge{font-size:8px;letter-spacing:1px;color:#96a8ba}.preview-body{display:grid;grid-template-columns:1fr 1fr;gap:18px;padding-top:18px}.preview-paper{padding:16px;border:1px solid #e6edf6;border-radius:7px;background:white}.preview-paper small{display:block;font-size:8px;color:#98a8b8;letter-spacing:1px}.preview-paper strong{display:block;font-size:15px;line-height:1.7;margin-top:10px}.preview-lines{display:grid;gap:5px;margin:14px 0}.preview-lines i{height:4px;border-radius:3px;background:#edf2f7}.preview-lines i:last-child{width:65%}.preview-chart{display:flex;align-items:flex-end;gap:7px;height:42px}.preview-chart span{flex:1;height:36%;background:#bae0ff;border-radius:3px 3px 0 0}.preview-chart span:nth-child(2){height:54%}.preview-chart span:nth-child(3){height:45%}.preview-chart span:nth-child(4){height:78%;background:#69b1ff}.preview-chart span:nth-child(5){height:67%;background:#4096ff}.preview-chart span:nth-child(6){height:100%;background:#1677ff}.preview-notes{padding:10px 0;display:flex;flex-direction:column;gap:14px}.preview-notes>span{display:flex;align-items:center;gap:6px;font-size:11px;color:#718399}.preview-notes>span svg{color:#1677ff}.preview-note{background:#eaf4ff;padding:14px;border-radius:8px;color:#1677ff;font-size:11px;line-height:1.8}.preview-note p{margin-top:6px;color:#547497}.login-card{background:#fff;border:1px solid #e6edf5;border-radius:18px;padding:36px;box-shadow:0 18px 60px #243e6010}.login-card-icon{height:50px;width:50px;display:grid;place-items:center;border-radius:13px;background:#e6f4ff;color:#1677ff;margin-bottom:20px}.login-card h2,.login-guide h2{font-size:25px;letter-spacing:-.5px;font-weight:700}.login-subtitle{font-size:13px;color:#8792a1;margin:9px 0 28px}.login-card label{display:block;font-size:13px;font-weight:600;margin:18px 0 9px}.login-field{height:48px;display:flex;align-items:center;gap:11px;padding:0 13px;border:1px solid #dfe6ef;border-radius:8px;color:#9aa7b7;background:#fcfdff}.login-field:focus-within{border-color:#1677ff;box-shadow:0 0 0 3px #1677ff0c}.login-field input{width:100%;min-width:0;border:0;outline:none;background:transparent;color:#1f2329;font-size:14px;height:100%}.login-field input::placeholder{color:#a7b1bf}.password-toggle{display:grid;place-items:center;background:transparent;color:#8190a3;padding:4px}.login-submit{display:flex;align-items:center;justify-content:center;gap:12px;width:100%;min-height:48px;margin-top:24px;border:0;border-radius:8px;background:#1677ff;color:#fff;font-size:14px;font-weight:600;transition:background .15s}.login-submit:hover:not(:disabled){background:#0958d9}.login-account-help{font-size:12px;line-height:1.9;text-align:center;color:#8a96a6;margin-top:22px}.login-private{border-top:1px solid #edf1f6;padding-top:19px;margin-top:24px;display:flex;align-items:center;justify-content:center;gap:6px;color:#91a1b4;font-size:10px}.login-error{color:#b42318;background:#fff3f2;border:1px solid #ffdedb;padding:10px 12px;border-radius:7px;font-size:12px;line-height:1.6;margin-top:14px}.login-service-error{color:#7b6644;background:#fff9ee;border:1px solid #f3e6c8;padding:10px 12px;border-radius:7px;font-size:12px;line-height:1.6;margin-top:14px}.login-service-error button{background:transparent;color:#1677ff;font-size:12px;padding:4px 0;display:block}.login-footer{width:min(1280px,100%);margin:0 auto;padding:20px 48px 28px;display:flex;justify-content:space-between;color:#9ca8b7;font-size:10px;letter-spacing:1px}.login-spinner{width:16px;height:16px;border:2px solid #ffffff60;border-top-color:#fff;border-radius:50%;animation:spin .8s linear infinite}.login-guide::backdrop{background:#172c4566;backdrop-filter:blur(4px)}.login-guide{position:fixed;inset:0;margin:auto;width:min(400px,calc(100% - 40px));color:#1f2329;background:#fff;border:1px solid #e6edf5;padding:32px;border-radius:17px;box-shadow:0 28px 90px #172c4533}.login-guide h2{font-size:22px}.login-guide p{font-size:13px;line-height:1.9;color:#7a8798;margin-top:12px}.guide-close{position:absolute;top:14px;right:14px;background:transparent;padding:5px;color:#93a0b0}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:900px){.login-content{gap:32px;padding:26px 28px 48px}.login-card{padding:28px}.login-story h1{font-size:32px}.preview-notes{display:none}.preview-body{grid-template-columns:1fr}.preview-paper{max-width:none}}@media(max-width:650px){.login-header{padding:22px 24px}.login-header-note{display:none}.login-content{display:flex;flex-direction:column;gap:26px;padding:24px 20px 40px}.login-story{width:100%;max-width:400px}.login-story h1{font-size:28px;margin:14px 0 10px}.login-intro{font-size:12px}.login-preview{display:none}.login-card{width:100%;max-width:400px;padding:26px;border-radius:14px}.login-card-icon{height:44px;width:44px;margin-bottom:17px}.login-card h2{font-size:23px}.login-footer{padding:18px 24px;gap:14px;flex-wrap:wrap;font-size:9px}.login-private{font-size:10px}.login-eyebrow{font-size:11px}}@media(prefers-reduced-motion:reduce){.login-spinner{animation:none}}
</style>
