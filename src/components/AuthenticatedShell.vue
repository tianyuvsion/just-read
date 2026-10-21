<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowUpRight, Bookmark, BookOpen, FlaskConical, FolderOpen, LayoutDashboard, MessageSquare, Plus, Sprout } from 'lucide-vue-next'
import { useResearchStore } from '../stores/research'
import { auth, logout } from '../services/auth'

const route = useRoute()
const store = useResearchStore()
const router = useRouter()
const signingOut = ref(false)
const logoutError = ref('')
async function signOut() {
  if (signingOut.value) return
  signingOut.value = true
  logoutError.value = ''
  try { await logout() }
  catch (error) { logoutError.value = error instanceof Error ? error.message : '退出失败，请重试。' }
  finally { signingOut.value = false }
}
void store.initialize()
watch(() => store.completedReportId, id => { if (id) void router.push(`/report/${id}`) })
const pageLabel = computed(() =>
  route.path.startsWith('/share/') ? '冻结分享' :
  route.path.startsWith('/report/') ? '报告阅读' :
  route.path === '/reports' ? '我的报告' :
  route.path === '/bookmarks' ? '我的书签' :
  route.path === '/chat' ? 'AI 对话' : '调研工作台'
)
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <RouterLink to="/" class="brand" aria-label="Just Read 首页"><span class="brand-mark"><BookOpen :size="22" /></span><span>just read<span class="brand-dot">.</span></span></RouterLink>
      <div class="sidebar-caption">少一点信息，更多一点洞察</div>
      <RouterLink to="/?new=1" class="new-research"><Plus :size="17" />新建调研 <span>↗</span></RouterLink>
      <div class="nav-label">工作空间</div>
      <nav class="main-nav" aria-label="主导航">
        <RouterLink to="/" :class="{ selected: route.path === '/' }"><LayoutDashboard :size="18" />调研工作台</RouterLink>
        <RouterLink to="/chat" :class="{ selected: route.path === '/chat' }"><MessageSquare :size="18" />AI 对话</RouterLink>
        <RouterLink to="/reports" :class="{ selected: route.path === '/reports' || route.path.startsWith('/report/') }"><FolderOpen :size="18" />我的报告<span class="nav-count">{{ store.reports.length }}</span></RouterLink>
        <RouterLink to="/bookmarks" :class="{ selected: route.path === '/bookmarks' }"><Bookmark :size="18" />我的书签</RouterLink>
      </nav>
      <div class="sidebar-bottom">
        <div class="prototype-card"><FlaskConical :size="19" /><strong>想法，正在发生。</strong><p>从一个好问题开始，<br>探索报告的新可能。</p><span>产品原型 <ArrowUpRight :size="13" /></span></div>
        <div class="profile"><div class="avatar">{{ (auth.user?.display_name || auth.user?.username || 'J').slice(0, 1).toUpperCase() }}</div><div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><small>已登录</small></div><button type="button" class="text-button" :disabled="signingOut" @click="signOut">{{ signingOut ? '退出中…' : '退出登录' }}</button></div>
      </div>
    </aside>
    <div class="main-shell">
      <header class="topbar"><div class="breadcrumb">工作空间 <span>/</span> <strong>{{ pageLabel }}</strong></div><div class="demo-pill"><span></span>{{ store.ready ? '服务已连接' : '正在连接服务' }} <button type="button" class="mobile-signout text-button" :disabled="signingOut" @click="signOut">退出登录</button><span class="version">A–D 工作区</span></div></header>
      <div v-if="logoutError" class="global-warning" role="alert">{{ logoutError }}</div>
      <div v-if="store.storageWarning" class="global-warning" role="status">{{ store.storageWarning }}</div>
      <div v-if="store.error" class="global-warning" role="alert">{{ store.error }} <button class="text-button" :disabled="store.initializing" @click="store.reconnect">重试连接</button> <button v-if="store.pendingBlocked" class="text-button" @click="store.dismissPending">清除本地等待记录</button></div>
      <RouterView />
      <footer class="site-footer"><span><Sprout :size="14" />让知识更易理解</span><span>JUST READ · BUILT FOR CURIOSITY</span></footer>
    </div>
  </div>
</template>

<style scoped>
.sidebar{overflow-y:auto}.profile>div:nth-child(2){min-width:0;flex:1}.profile .avatar{flex-shrink:0}.profile strong,.profile small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block}.profile small{font-size:10px}.profile button{font-size:11px;white-space:nowrap}.mobile-signout{display:none}@media(max-width:640px){.sidebar{overflow:visible}.mobile-signout{display:inline-block;font-size:11px}}
</style>
