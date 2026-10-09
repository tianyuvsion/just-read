<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowRight, ArrowUpRight, Bookmark, Check, FileText, LoaderCircle, Search, Sparkles, X } from 'lucide-vue-next'
import ReportCard from '../components/ReportCard.vue'
import DocumentUpload from '../components/DocumentUpload.vue'
import SourceLibrary from '../components/SourceLibrary.vue'
import TaskHistory from '../components/TaskHistory.vue'
import { useResearchStore } from '../stores/research'
import { generationSteps } from '../services/research'

const store = useResearchStore()
const route = useRoute()
const question = ref('')
const search = ref('')
const category = ref('全部')
const textarea = ref<HTMLTextAreaElement>()
const sourceIds = ref<string[]>([])
const reader = ref('')
const scope = ref('')
const depth = ref<'brief'|'deep'>('deep')
const enable3d = ref(false)
const isHome = computed(() => route.path === '/')
const isBookmarks = computed(() => route.path === '/bookmarks')
const prompts = ['AI Agent 的技术路线与应用前景', 'RAG 与微调，企业应该如何选择？', 'AI 调研报告如何做好可视化？']
const categories = computed(() => ['全部', ...new Set(store.reports.map(r => r.category))])
const filteredReports = computed(() => store.reports.filter(r => (category.value === '全部' || category.value === r.category) && `${r.title} ${r.summary} ${r.chapters.flatMap(c => [c.title, ...c.paragraphs]).join(' ')}`.toLowerCase().includes(search.value.toLowerCase().trim())))
const visibleReports = computed(() => isHome.value ? filteredReports.value.slice(0, 3) : filteredReports.value)
const bookmarks = computed(() => store.reports.flatMap(r => r.chapters.filter(c => r.bookmarks.includes(c.id)).map(c => ({ report: r, chapter: c }))).filter(b => `${b.report.title} ${b.chapter.title} ${b.chapter.paragraphs.join(' ')}`.toLowerCase().includes(search.value.toLowerCase().trim())))

watch(() => route.fullPath, async () => {
  search.value = ''; category.value = '全部'
  if (route.query.new) { question.value = ''; await nextTick(); textarea.value?.focus() }
}, { immediate: true })

function usePrompt(prompt: string) { question.value = prompt; textarea.value?.focus() }
async function generate() {
  await store.generate({ question: question.value, depth: depth.value, source_ids: sourceIds.value, reader: reader.value, scope: scope.value, enable_3d: enable3d.value })
}
</script>

<template>
  <main class="workspace">
    <template v-if="isHome">
      <section class="hero">
        <div class="eyebrow"><span></span> YOUR NEXT INSIGHT STARTS HERE</div>
        <h1>让复杂，<span>变清晰。</span><span class="hero-spark" aria-hidden="true">✳</span></h1>
        <p>提出一个问题，把纷繁的信息，变成值得阅读的调研报告。</p>
      </section>
      <section class="composer" aria-label="新建调研">
        <div class="composer-label"><Sparkles :size="17" /><span>今天，你想深入了解什么？</span><span class="tiny-badge">AI RESEARCH</span></div>
        <form @submit.prevent="generate">
          <label class="sr-only" for="research-question">调研问题</label>
          <textarea id="research-question" ref="textarea" v-model="question" :disabled="store.running" maxlength="1000" placeholder="例如：深入分析 AI Agent 的技术路线、应用场景和未来发展趋势…" @keydown.ctrl.enter.prevent="generate" @keydown.meta.enter.prevent="generate"></textarea>
          <div class="research-options field-grid"><label>目标读者<input v-model="reader" :disabled="store.running" maxlength="200" placeholder="例如：具有基础知识的产品团队" aria-label="目标读者"/></label><label>范围与重点<input v-model="scope" :disabled="store.running" maxlength="1000" placeholder="例如：技术路线、适用条件、成本" aria-label="研究范围"/></label><label>研究深度<select v-model="depth" :disabled="store.running" aria-label="研究深度"><option value="brief">简明</option><option value="deep">深入</option></select></label><label class="checkbox-field"><input v-model="enable3d" type="checkbox" :disabled="store.running" aria-label="启用概念三维"/> 启用概念三维（非真实尺寸）</label></div>
          <SourceLibrary v-model="sourceIds" :disabled="store.running||!store.ready" />
          <div class="composer-toolbar"><span class="input-count">{{ question.length }}/1000</span><button type="submit" class="primary-button" :disabled="!question.trim() || store.running || !store.ready || !store.generationReady"><LoaderCircle v-if="store.running" class="spin" :size="16" /><template v-else>开始调研 <ArrowRight :size="17" /></template><template v-if="store.running">生成中</template></button></div>
        </form>
        <DocumentUpload :disabled="store.running || !store.ready" />
        <div class="composer-note"><span class="status-dot"></span>{{ store.ready && !store.generationReady ? '服务端尚未配置模型 · 可继续导入和阅读文档' : '由服务端生成 · 关闭页面后可恢复任务' }}<span>Ctrl / ⌘ + Enter</span></div>
      </section>
      <div class="prompt-row"><span>试试这些</span><button v-for="prompt in prompts" :key="prompt" :disabled="store.running" @click="usePrompt(prompt)">{{ prompt }}<ArrowUpRight :size="12" /></button></div>
    </template>

    <section v-if="store.running" class="generation-panel" aria-live="polite">
      <div class="generation-heading"><div><LoaderCircle class="spin" :size="17" /><strong>正在生成调研报告</strong></div><button class="text-button" :disabled="store.cancelling" @click="store.cancel"><X :size="14" />{{ store.cancelling ? '正在取消…' : '取消生成' }}</button></div>
      <p>{{ store.activeQuestion }}</p><p>{{ store.message }}</p>
      <div class="generation-steps"><div v-for="(label, index) in generationSteps" :key="label" :class="{ done: index < store.step, current: index === store.step }"><span><Check v-if="index < store.step" :size="13" /><template v-else>{{ index + 1 }}</template></span>{{ label }}</div></div>
    </section>
    <TaskHistory />

    <section class="reports-section">
      <div class="section-heading"><div><div v-if="!isHome" class="eyebrow">YOUR KNOWLEDGE, ORGANIZED</div><h2 :class="{ 'page-title': !isHome }">{{ isBookmarks ? '我的书签' : isHome ? '最近的调研' : '我的报告' }}<span class="section-count">{{ isBookmarks ? store.bookmarkCount : store.reports.length }}</span></h2><p>{{ isBookmarks ? '留住值得回看的段落，让洞察随时可达。' : isHome ? '每一次好奇，都值得留下答案。' : '你提出的问题，和它们逐渐清晰的答案。' }}</p></div><RouterLink v-if="isHome" to="/reports" class="text-link">全部报告 <ArrowRight :size="15" /></RouterLink></div>
      <div v-if="!isHome" class="library-toolbar"><div v-if="!isBookmarks" class="filter-tabs"><button v-for="item in categories" :key="item" :class="{ active: item === category }" @click="category = item">{{ item }}</button></div><label class="search-input"><Search :size="16" /><input v-model="search" :placeholder="isBookmarks ? '搜索书签内容…' : '搜索报告全文…'" aria-label="搜索报告或书签" /></label></div>
      <div v-if="isBookmarks" class="bookmark-list"><RouterLink v-for="item in bookmarks" :key="`${item.report.id}-${item.chapter.id}`" :to="`/report/${item.report.id}?chapter=${item.chapter.id}`" class="bookmark-item"><Bookmark :size="20" /><div><small>{{ item.report.title }}</small><h3>{{ item.chapter.title }}</h3><p>{{ item.chapter.paragraphs[0] }}</p></div><ArrowUpRight :size="18" /></RouterLink><div v-if="!bookmarks.length" class="empty-state"><Bookmark :size="30" /><h3>{{ search ? '没有找到匹配的书签' : '把有启发的章节留在这里' }}</h3><p>{{ search ? '试试其他关键词。' : '阅读报告时，点击章节右侧的书签图标即可收藏。' }}</p><RouterLink to="/reports" class="text-link">浏览报告 <ArrowRight :size="15" /></RouterLink></div></div>
      <template v-else><div class="report-grid"><ReportCard v-for="(report, index) in visibleReports" :key="report.id" :report="report" :index="index" /></div><div v-if="!visibleReports.length" class="empty-state"><Search :size="30" /><h3>没有找到匹配的报告</h3><p>试试其他关键词，或切换分类。</p><button class="text-button" @click="search = ''; category = '全部'">清除筛选</button></div></template>
    </section>
    <section v-if="isHome" class="workflow-strip"><div class="workflow-intro"><span class="small-icon"><FileText :size="19" /></span><div><strong>从问题，到洞察</strong><p>让阅读回归理解本身</p></div></div><div><span>01</span>提出问题</div><ArrowRight :size="15" /><div><span>02</span>结构化调研</div><ArrowRight :size="15" /><div><span>03</span>可视化阅读</div></section>
  </main>
</template>
