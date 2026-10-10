<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Report } from '../types'
const props = defineProps<{ data: Report['chart']; meta: NonNullable<Report['chartMeta']> }>()
const mode = ref<'chart' | 'table'>('chart')
const selected = ref(-1)
const minimum = computed(() => Math.min(0, ...props.data.map(d => d.value)))
const maximum = computed(() => Math.max(0, ...props.data.map(d => d.value)))
const scale = computed(() => Math.max(1, ...props.data.map(d => Math.abs(d.value))))
const normalizedMinimum = computed(() => minimum.value / scale.value)
const range = computed(() => maximum.value / scale.value - normalizedMinimum.value || 1)
const colors = ['#1677ff', '#69b1ff', '#adc6ff']
function bar(value: number, index: number) {
  return { position: 'absolute' as const, left: `${(Math.min(0, value / scale.value) - normalizedMinimum.value) / range.value * 100}%`,
    width: `${Math.abs(value / scale.value) / range.value * 100}%`, background: colors[index % colors.length] }
}
</script>

<template>
  <figure class="report-chart">
    <div class="chart-heading"><div><strong>{{ meta.title }}</strong><p>{{ meta.note }} · 单位：{{ meta.unit }}</p></div><div class="chart-switch" aria-label="图表视图"><button :class="{ active: mode === 'chart' }" :aria-pressed="mode === 'chart'" @click="mode = 'chart'">图表</button><button :class="{ active: mode === 'table' }" :aria-pressed="mode === 'table'" @click="mode = 'table'">数据</button></div></div>
    <div v-if="mode === 'chart'" class="bar-chart"><button v-for="(item, index) in data" :key="index" class="bar-row" :class="{ highlighted: selected === index }" :aria-label="`${item.label}：${item.value} ${meta.unit}`" @click="selected = selected === index ? -1 : index"><span>{{ item.label }}</span><span class="bar-track" style="position:relative"><span class="bar-fill" :style="bar(item.value, index)"></span></span><strong>{{ item.value }}</strong></button><div class="chart-axis"><span>{{ minimum }}</span><span>{{ maximum }}</span></div><p class="chart-selection" aria-live="polite">{{ selected >= 0 && data[selected] ? `${data[selected]!.label}：${data[selected]!.value} ${meta.unit}` : '点击条形，查看对应数据。' }}</p></div>
    <table v-else><caption class="sr-only">{{ meta.title }}</caption><thead><tr><th>项目</th><th>{{ meta.unit }}</th></tr></thead><tbody><tr v-for="(item, index) in data" :key="index"><td>{{ item.label }}</td><td>{{ item.value }}</td></tr></tbody></table>
    <figcaption>{{ meta.note }}</figcaption>
  </figure>
</template>
