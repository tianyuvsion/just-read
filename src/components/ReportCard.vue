<script setup lang="ts">
import { ArrowUpRight, Clock3, FileText } from 'lucide-vue-next'
import type { Report } from '../types'
defineProps<{ report: Report; index: number }>()
</script>

<template>
  <RouterLink :to="`/report/${report.id}`" class="report-card">
    <div class="card-art" :class="`art-${index % 3}`" aria-hidden="true">
      <template v-if="index % 3 === 0"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="orbit orbit-three"></div><div class="orbit-center">✳</div><span class="art-caption">CONNECT THE DOTS</span></template>
      <template v-else-if="index % 3 === 1"><div class="art-bars"><i></i><i></i><i></i><i></i><i></i><i></i></div><span class="art-caption">FROM DATA TO INSIGHT</span></template>
      <template v-else><div class="art-grid"><i></i><i></i><i></i><i></i></div><span class="art-caption">A CLEARER PERSPECTIVE</span></template>
      <span class="art-tag">{{ report.source === 'import' ? '导入文档' : '示例报告' }}</span><span class="card-open"><ArrowUpRight :size="18" /></span>
    </div>
    <div class="report-card-body"><div class="category">{{ report.category }}</div><h3>{{ report.title }}</h3><p>{{ report.chapters.length }} 个章节 · {{ report.chart.length ? '含交互图表' : '本地文档' }}</p><div class="card-meta"><span><FileText :size="13" />{{ new Date(report.createdAt).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) }}</span><span><Clock3 :size="13" />{{ report.minutes }} 分钟阅读</span></div></div>
  </RouterLink>
</template>
