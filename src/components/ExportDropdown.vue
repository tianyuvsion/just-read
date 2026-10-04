<script setup lang="ts">
import { ref } from 'vue'
import { Download, LoaderCircle } from 'lucide-vue-next'
import type { Report } from '../types'
import { exportDocument, type ExportFormat } from '../services/documentExport'

const props = defineProps<{ report: Report }>()
const format = ref<ExportFormat>('pdf')
const busy = ref(false)
const status = ref('')
const error = ref('')
async function download() {
  if (busy.value) return
  busy.value = true
  status.value = ''
  error.value = ''
  try {
    await exportDocument(props.report, format.value)
    status.value = '文件已生成，下载已开始。'
  } catch { error.value = '导出失败，请重试或选择其他格式。' }
  finally { busy.value = false }
}
</script>

<template>
  <div class="export-control">
    <div class="export-actions">
      <select v-model="format" aria-label="导出格式" :disabled="busy">
        <option value="pdf">PDF（.pdf）</option>
        <option value="docx">Word（.docx）</option>
        <option value="md">Markdown（.md）</option>
        <option value="html">HTML（.html）</option>
      </select>
      <button class="secondary-button" :disabled="busy" @click="download"><LoaderCircle v-if="busy" class="spin" :size="15" /><Download v-else :size="15" />{{ busy ? '导出中…' : '导出' }}</button>
    </div>
    <p v-if="format === 'pdf'" class="export-hint">PDF 为图片版，支持中文，文字不可选中。</p>
    <p v-if="status" role="status">{{ status }}</p>
    <p v-if="error" class="upload-error" role="alert">{{ error }}</p>
  </div>
</template>
