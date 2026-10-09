<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Upload, LoaderCircle } from 'lucide-vue-next'
import { documentAccept, importDocument } from '../services/import'
import { useResearchStore } from '../stores/research'

defineProps<{ disabled?: boolean }>()
const router = useRouter()
const store = useResearchStore()
const busy = ref(false)
const error = ref('')
const filename = ref('')
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || busy.value) return
  busy.value = true
  error.value = ''
  filename.value = file.name
  try {
    const report = await importDocument(file)
    const saved = await store.addReport(report)
    await router.push(`/report/${saved.id}`)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '导入失败，请检查文件后重试。'
  } finally { busy.value = false; input.value = '' }
}
</script>

<template>
  <div class="document-upload">
    <label for="document-file" class="upload-label"><Upload :size="16" />上传文档</label>
    <input id="document-file" type="file" :accept="documentAccept" :disabled="disabled || busy" aria-describedby="document-help" @change="upload" />
    <p id="document-help">支持 PDF、Word（.docx）、Markdown（.md），最大 20 MB。本地提取正文后同步至服务端并打开阅读。</p>
    <p v-if="busy" class="upload-progress" role="status"><LoaderCircle class="spin" :size="14" />正在导入 {{ filename }}…</p>
    <p v-if="error" class="upload-error" role="alert">{{ error }}</p>
  </div>
</template>
