<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, ArrowRight, Bookmark, Check, Clock3, Download, FileText, Search } from 'lucide-vue-next'
import { useResearchStore } from '../stores/research'
import { exportReport } from '../services/export'
import ReportChart from '../components/ReportChart.vue'

const route = useRoute()
const store = useResearchStore()
const report = computed(() => store.reports.find(item => item.id === route.params.id))
const query = ref('')
const activeChapter = ref('overview')
const exported = ref(false)
let exportTimer: ReturnType<typeof setTimeout> | undefined
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
function download() {
  if (!report.value) return
  exportReport(report.value)
  exported.value = true
  clearTimeout(exportTimer)
  exportTimer = setTimeout(() => { exported.value = false }, 2400)
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
onBeforeUnmount(() => { observer?.disconnect(); clearTimeout(exportTimer) })
</script>

<template>
  <main v-if="report" class="reader">
    <div class="reader-toolbar"><RouterLink to="/reports" class="text-link"><ArrowLeft :size="15" />返回报告</RouterLink><button class="secondary-button" @click="download"><Check v-if="exported" :size="15" /><Download v-else :size="15" />{{ exported ? '已导出' : '导出 HTML' }}</button></div>
    <div class="reader-layout"><aside class="report-toc"><div class="nav-label">报告目录</div><nav aria-label="报告目录"><button v-for="(chapter, index) in report.chapters" :key="chapter.id" :class="{ active: activeChapter === chapter.id }" @click="query = ''; nextTick(() => jump(chapter.id))"><span>0{{ index + 1 }}</span>{{ chapter.title }}<Bookmark v-if="report.bookmarks.includes(chapter.id)" :size="12" /></button></nav><div class="reading-tip"><FileText :size="18" /><p>好的调研，<br>让下一步更清晰。</p><small>JUST READ</small></div></aside>
      <article class="report-document"><div class="report-kicker">{{ report.category }}<span>DEMO REPORT</span></div><h1>{{ report.title }}</h1><div class="report-metadata"><span>{{ new Date(report.createdAt).toLocaleDateString('zh-CN') }}</span><span><Clock3 :size="14" />{{ report.minutes }} 分钟阅读</span><span>{{ report.chapters.length }} 个章节</span></div><div class="report-disclaimer"><span class="status-dot"></span>演示内容：固定模板生成，未联网检索；图表使用虚构数据。</div><p class="report-summary">{{ report.summary }}</p><label class="search-input reader-search"><Search :size="17" /><input v-model="query" placeholder="在这份报告中搜索…" aria-label="报告内全文搜索" /><span v-if="query">{{ matchedChapters.length }} 个章节</span></label>
        <section v-for="chapter in matchedChapters" :id="`chapter-${chapter.id}`" :key="chapter.id" class="report-chapter"><div class="chapter-heading"><h2><span>0{{ report.chapters.indexOf(chapter) + 1 }}</span>{{ chapter.title }}</h2><button class="bookmark-button" :class="{ saved: report.bookmarks.includes(chapter.id) }" :aria-label="`${report.bookmarks.includes(chapter.id) ? '取消收藏' : '收藏'}：${chapter.title}`" :aria-pressed="report.bookmarks.includes(chapter.id)" @click="store.toggleBookmark(report.id, chapter.id)"><Bookmark :size="18" /></button></div><p v-for="(paragraph, index) in chapter.paragraphs" :key="index"><template v-for="(part, partIndex) in parts(paragraph)" :key="partIndex"><mark v-if="part.match">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></p><ReportChart v-if="chapter.id === 'comparison'" :data="report.chart" /></section>
        <div v-if="!matchedChapters.length" class="empty-state"><Search :size="28" /><h3>未找到相关内容</h3><button class="text-button" @click="query = ''">清除搜索</button></div>
        <div class="report-end"><span>✳</span><p>阅读的终点，是新思考的起点。</p><RouterLink to="/?new=1" class="text-link">发起下一次调研 <ArrowRight :size="15" /></RouterLink></div>
      </article>
    </div>
  </main>
  <main v-else class="empty-state missing-report"><FileText :size="36" /><h1>这份报告不在当前工作空间</h1><p>报告保存在本机浏览器中，请返回报告列表查看。</p><RouterLink to="/reports" class="primary-button">返回我的报告</RouterLink></main>
</template>
