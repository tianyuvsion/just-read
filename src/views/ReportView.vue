<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, ArrowRight, Bookmark, Clock3, FileText, Search } from 'lucide-vue-next'
import { useResearchStore } from '../stores/research'
import ExportDropdown from '../components/ExportDropdown.vue'
import ReportChart from '../components/ReportChart.vue'
import ReportWorkspace from '../components/ReportWorkspace.vue'
import { ApiError, getSharedReport, reportNotice } from '../services/research'
import type { Report } from '../types'

const route = useRoute()
const store = useResearchStore()
const sharedReport = ref<Report>()
const isShared = computed(() => !!route.params.token)
const report = computed(() => isShared.value ? sharedReport.value : store.reports.find(item => item.id === route.params.id))
const query = ref('')
const activeChapter = ref('overview')
const loading = ref(true)
const loadError = ref('')
const notFound = ref(false)
const chartChapterId = computed(() => report.value?.chapters.find(c => c.id === 'comparison')?.id ?? report.value?.chapters[0]?.id)
watch(() => [route.params.id, route.params.token], async ([id, token]) => {
  loading.value = true; loadError.value = ''; notFound.value = false
  sharedReport.value = undefined
  try { if (token) sharedReport.value = await getSharedReport(String(token)); else await store.loadReport(String(id)) }
  catch (cause) { notFound.value = cause instanceof ApiError && cause.status === 404; loadError.value = cause instanceof Error ? cause.message : '报告加载失败。' }
  finally { loading.value = false; await observeChapters(); if (typeof route.query.chapter === 'string') jump(route.query.chapter) }
}, { immediate: true })
let observer: IntersectionObserver | undefined
const matchedChapters = computed(() => report.value?.chapters.filter(c => `${c.title} ${c.paragraphs.join(' ')}`.toLowerCase().includes(query.value.trim().toLowerCase())) ?? [])

function parts(text: string) {
  const needle = query.value.trim()
  if (!needle) return [{ text, match: false }]
  const result: { text: string; match: boolean }[] = []
  let cursor = 0
  let index = text.toLowerCase().indexOf(needle.toLowerCase())
  while (index !== -1) {
    result.push({ text: text.slice(cursor, index), match: false }, { text: text.slice(index, index + needle.length), match: true })
    cursor = index + needle.length
    index = text.toLowerCase().indexOf(needle.toLowerCase(), cursor)
  }
  result.push({ text: text.slice(cursor), match: false })
  return result
}
function jump(id: string) {
  document.getElementById(`chapter-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  activeChapter.value = id
}
async function observeChapters() {
  await nextTick()
  observer?.disconnect()
  observer = new IntersectionObserver(entries => {
    const visible = entries.find(e => e.isIntersecting)
    if (visible) activeChapter.value = visible.target.id.replace('chapter-', '')
  }, { rootMargin: '-90px 0px -55% 0px' })
  document.querySelectorAll('.report-chapter').forEach(el => observer?.observe(el))
}
onMounted(async () => { await observeChapters(); if (typeof route.query.chapter === 'string') jump(route.query.chapter) })
watch(matchedChapters, observeChapters)
onBeforeUnmount(() => { observer?.disconnect() })
</script>

<template>
  <main v-if="report" class="reader">
    <div class="reader-toolbar"><RouterLink to="/reports" class="text-link"><ArrowLeft :size="15" />返回报告</RouterLink><ExportDropdown :key="report.id" :report="report" /></div>
    <p v-if="isShared" class="workspace-notice">此为冻结分享版本 v{{ report.version??1 }}，阅读不会修改原报告。</p>
    <ReportWorkspace v-if="report.workflow" :key="`${report.id}-${isShared}`" :report="report" :readonly="isShared" @saved="store.upsert" />
    <div class="reader-layout"><aside class="report-toc"><div class="nav-label">报告目录</div><nav aria-label="报告目录"><button v-for="(chapter, index) in report.chapters" :key="chapter.id" :class="{ active: activeChapter === chapter.id }" @click="query = ''; nextTick(() => jump(chapter.id))"><span>0{{ index + 1 }}</span>{{ chapter.title }}<Bookmark v-if="report.bookmarks.includes(chapter.id)" :size="12" /></button></nav><div class="reading-tip"><FileText :size="18" /><p>好的调研，<br>让下一步更清晰。</p><small>JUST READ</small></div></aside>
      <article class="report-document"><div class="report-kicker">{{ report.category }}<span>{{ report.source === 'import' ? 'IMPORTED DOCUMENT' : report.source === 'demo' ? 'DEMO REPORT' : 'RESEARCH REPORT' }}</span></div><h1>{{ report.title }}</h1><div class="report-metadata"><span>{{ new Date(report.createdAt).toLocaleDateString('zh-CN') }}</span><span><Clock3 :size="14" />{{ report.minutes }} 分钟阅读</span><span>{{ report.chapters.length }} 个章节</span></div><div class="report-disclaimer"><span class="status-dot"></span>{{ reportNotice(report) }}</div><p class="report-summary">{{ report.summary }}</p><label class="search-input reader-search"><Search :size="17" /><input v-model="query" placeholder="在这份报告中搜索…" aria-label="报告内全文搜索" /><span v-if="query">{{ matchedChapters.length }} 个章节</span></label>
        <section v-for="chapter in matchedChapters" :id="`chapter-${chapter.id}`" :key="chapter.id" class="report-chapter"><div class="chapter-heading"><h2><span>0{{ report.chapters.indexOf(chapter) + 1 }}</span>{{ chapter.title }}</h2><button v-if="!isShared" class="bookmark-button" :class="{ saved: report.bookmarks.includes(chapter.id) }" :aria-label="`${report.bookmarks.includes(chapter.id) ? '取消收藏' : '收藏'}：${chapter.title}`" :aria-pressed="report.bookmarks.includes(chapter.id)" :disabled="store.bookmarkBusy.includes(report.id)" @click="store.toggleBookmark(report.id, chapter.id)"><Bookmark :size="18" /></button></div><p v-for="(paragraph, index) in chapter.paragraphs" :key="index"><template v-for="(part, partIndex) in parts(paragraph)" :key="partIndex"><mark v-if="part.match">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></p><ReportChart v-if="chapter.id === chartChapterId && report.chart.length && report.chartMeta" :data="report.chart" :meta="report.chartMeta" /></section>
        <div v-if="!matchedChapters.length" class="empty-state"><Search :size="28" /><h3>未找到相关内容</h3><button class="text-button" @click="query = ''">清除搜索</button></div>
        <div class="report-end"><span>✳</span><p>阅读的终点，是新思考的起点。</p><RouterLink to="/?new=1" class="text-link">发起下一次调研 <ArrowRight :size="15" /></RouterLink></div>
      </article>
    </div>
  </main>
  <main v-else-if="loading" class="empty-state"><p>正在加载报告…</p></main>
  <main v-else-if="loadError && !notFound" class="empty-state"><h1>暂时无法加载报告</h1><p>{{ loadError }}</p><button class="primary-button" @click="store.reconnect">重试连接</button></main>
  <main v-else class="empty-state missing-report"><FileText :size="36" /><h1>这份报告不在当前工作空间</h1><p>未找到当前会话可访问的报告，请返回报告列表查看。</p><RouterLink to="/reports" class="primary-button">返回我的报告</RouterLink></main>
</template>
