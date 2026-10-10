<script setup lang="ts">
import { ref, watch } from 'vue'
import { addSource, getSource, listSources } from '../services/research'
import type { SourceMaterial } from '../types'
import { useResearchStore } from '../stores/research'
const store = useResearchStore()
const props = defineProps<{ modelValue: string[]; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [ids: string[]] }>()
const sources = ref<SourceMaterial[]>([])
const selected = ref<SourceMaterial>()
const busy = ref(false)
const error = ref('')
function toggle(id: string, checked: boolean) { emit('update:modelValue', checked ? [...new Set([...props.modelValue, id])] : props.modelValue.filter(item => item !== id)) }
async function refresh() { try { sources.value = (await listSources()).sources } catch (e) { error.value = e instanceof Error ? e.message : '材料加载失败' } }
async function inspect(id: string) { try { selected.value = await getSource(id) } catch (e) { error.value = String(e) } }
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files?.length) return
  busy.value = true; error.value = ''
  try {
    for (const file of Array.from(input.files)) {
      if (file.size > 20 * 1024 * 1024) throw new Error(`${file.name} 超过 20 MB。`)
      const data = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader(); reader.onerror = () => reject(new Error('文件读取失败'))
        reader.onload = () => resolve(String(reader.result).split(',')[1] ?? ''); reader.readAsDataURL(file)
      })
      const source = await addSource({ name: file.name, media_type: file.type || 'application/octet-stream', content_base64: data })
      sources.value = [source, ...sources.value.filter(item => item.id !== source.id)]
      toggle(source.id, true); selected.value = source
    }
  } catch (e) { error.value = e instanceof Error ? e.message : '材料上传失败' }
  finally { busy.value = false; input.value = '' }
}
watch(() => store.ready, value => { if (value) void refresh() }, { immediate: true })
</script>
<template>
  <details class="source-library" open>
    <summary>研究材料 <span class="tiny-badge">已选择 {{ modelValue.length }} 份</span></summary>
    <p class="muted">研究会按问题从选中的材料中选取片段。支持 PDF、DOCX、Markdown、文本、JSON 和 CSV；原文件与页码/段落定位保存在当前会话。</p>
    <label class="source-upload">添加研究材料<input type="file" multiple accept=".pdf,.docx,.md,.markdown,.txt,.json,.csv" aria-label="添加研究材料" :disabled="disabled || busy" @change="upload" /></label>
    <p v-if="busy" role="status">正在解析材料…</p><p v-if="error" class="upload-error" role="alert">{{ error }}</p>
    <div class="source-list"><div v-for="item in sources" :key="item.id" class="source-row"><label><input type="checkbox" :checked="modelValue.includes(item.id)" :disabled="disabled" :aria-label="`使用材料：${item.name}`" @change="toggle(item.id, ($event.target as HTMLInputElement).checked)" />{{ item.name }}</label><button type="button" class="text-button" @click="inspect(item.id)">查看定位</button></div></div>
    <p v-if="!sources.length && !busy" class="muted">尚未添加研究材料，也可以仅根据问题开始研究。</p>
    <details v-if="selected" class="source-preview" open><summary>{{ selected.name }} · 解析结果</summary><p v-for="warning in selected.warnings" :key="warning" class="muted">{{ warning }}</p><a class="text-link" :href="`/api/v1/sources/${encodeURIComponent(selected.id)}/download`" download>下载原文件</a><div v-for="chunk in selected.chunks?.slice(0, 12)" :key="chunk.id" class="evidence-card"><small>{{ chunk.page ? `第 ${chunk.page} 页` : chunk.paragraph ? `第 ${chunk.paragraph} 段` : chunk.id }}</small><p>{{ chunk.text }}</p></div><p class="muted">原件完整保留；研究按问题选取片段，当前预览最多 12 项。</p></details>
  </details>
</template>
