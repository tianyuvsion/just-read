<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowUpRight, BookOpen, FlaskConical, FolderOpen, LayoutDashboard, Plus, Sprout } from 'lucide-vue-next'
import { useResearchStore } from './stores/research'

const route = useRoute()
const store = useResearchStore()
const pageLabel = computed(() => route.path.startsWith('/report/') ? '报告阅读' : route.path === '/reports' ? '我的报告' : route.path === '/bookmarks' ? '我的书签' : '调研工作台')
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
        <RouterLink to="/reports" :class="{ selected: route.path === '/reports' || route.path.startsWith('/report/') }"><FolderOpen :size="18" />我的报告<span class="nav-count">{{ store.reports.length }}</span></RouterLink>
      </nav>
      <div class="sidebar-bottom">
        <div class="prototype-card"><FlaskConical :size="19" /><strong>想法，正在发生。</strong><p>从一个好问题开始，<br>探索报告的新可能。</p><span>产品原型 <ArrowUpRight :size="13" /></span></div>
        <div class="profile"><div class="avatar">J</div><div><strong>探索者</strong><small>本地工作空间</small></div><span class="online-dot"></span></div>
      </div>
    </aside>
    <div class="main-shell">
      <header class="topbar"><div class="breadcrumb">工作空间 <span>/</span> <strong>{{ pageLabel }}</strong></div><div class="demo-pill"><span></span>演示模式 <span class="version">MVP 0.1</span></div></header>
      <div v-if="store.storageWarning" class="global-warning" role="status">{{ store.storageWarning }}</div>
      <RouterView />
      <footer class="site-footer"><span><Sprout :size="14" />让知识更易理解</span><span>JUST READ · BUILT FOR CURIOSITY</span></footer>
    </div>
  </div>
</template>
