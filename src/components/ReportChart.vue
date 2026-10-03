<script setup lang="ts">
import { computed, ref } from 'vue'
defineProps<{ data: { label: string; value: number }[] }>()
const mode = ref<'chart' | 'table'>('chart')
const selected = ref(-1)
const colors = computed(() => ['#1677ff', '#69b1ff', '#adc6ff'])
</script>

<template>
  <figure class="report-chart">
    <div class="chart-heading"><div><strong>不同路径的对比视图</strong><p>演示评分 · 虚构数据 · 0–100</p></div><div class="chart-switch" aria-label="图表视图"><button :class="{ active: mode === 'chart' }" :aria-pressed="mode === 'chart'" @click="mode = 'chart'">图表</button><button :class="{ active: mode === 'table' }" :aria-pressed="mode === 'table'" @click="mode = 'table'">数据</button></div></div>
    <div v-if="mode === 'chart'" class="bar-chart"><button v-for="(item, index) in data" :key="item.label" class="bar-row" :class="{ highlighted: selected === index }" :aria-label="`${item.label}：${item.value} 分，虚构演示评分`" @click="selected = selected === index ? -1 : index"><span>{{ item.label }}</span><span class="bar-track"><span class="bar-fill" :style="{ width: `${Math.max(0, Math.min(100, item.value))}%`, background: colors[index % colors.length] }"></span></span><strong>{{ item.value }}</strong></button><div class="chart-axis"><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></div><p class="chart-selection" aria-live="polite">{{ selected >= 0 && data[selected] ? `${data[selected]!.label}：示例评分 ${data[selected]!.value} / 100` : '点击条形，查看对应示例评分。' }}</p></div>
    <table v-else><caption class="sr-only">虚构方案演示评分</caption><thead><tr><th>方案</th><th>示例评分</th></tr></thead><tbody><tr v-for="item in data" :key="item.label"><td>{{ item.label }}</td><td>{{ item.value }} / 100</td></tr></tbody></table>
    <figcaption>仅用于验证可视化交互，不构成实际方案评估。</figcaption>
  </figure>
</template>
