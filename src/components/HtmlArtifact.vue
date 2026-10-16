<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { Code2, Download, ExternalLink, X } from 'lucide-vue-next'

const props = defineProps<{ content: string; streaming?: boolean }>()
const dialog = ref<HTMLDialogElement>()
const previewOpen = ref(false)
const mobile = ref(false)
const html = computed(() => {
  const start = props.content.search(/<!doctype\s+html|<html\b/i)
  if (start < 0) return ''
  return props.content.slice(start).replace(/\s*```\s*$/, '').trim()
})
const complete = computed(() => /<\/html>\s*(?:<script\b[^>]*>[\s\S]*?<\/script>\s*)*$/i.test(html.value))
// Generated code runs in an opaque-origin iframe and cannot contact the app or other servers.
const previewHtml = computed(() => {
  const policy = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'">`
  return /<head\b/i.test(html.value)
    ? html.value.replace(/<head\b[^>]*>/i, match => match + policy)
    : html.value.replace(/<html\b[^>]*>/i, match => match + '<head>' + policy + '</head>')
})
async function preview() {
  previewOpen.value = true
  await nextTick()
  dialog.value?.showModal()
}
function download() {
  const url = URL.createObjectURL(new Blob([html.value], { type: 'text/html;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `JustREAD-${new Date().toISOString().slice(0, 10)}.html`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
</script>

<template>
  <div class="html-artifact">
    <div class="artifact-top"><Code2 :size="21" /><div><strong>交互式 HTML 报告</strong><small>{{ streaming ? '正在编写网页…' : complete ? '网页已生成，可以预览或下载' : '代码尚未完整，请重新生成' }}</small></div><span>{{ (content.length / 1000).toFixed(1) }}k 字符</span></div>
    <div class="artifact-actions">
      <button type="button" :disabled="streaming || !complete" @click="preview"><ExternalLink :size="14" />预览网页</button>
      <button type="button" :disabled="streaming || !complete" @click="download"><Download :size="14" />下载 HTML</button>
    </div>
    <details><summary>查看代码</summary><pre>{{ content }}</pre></details>
    <dialog ref="dialog" class="artifact-dialog" aria-label="生成的报告预览" @close="previewOpen = false" @click="($event.target === dialog) && dialog?.close()">
      <div class="preview-toolbar"><strong>报告预览</strong><div><button type="button" :aria-pressed="!mobile" @click="mobile = false">桌面</button><button type="button" :aria-pressed="mobile" @click="mobile = true">手机</button><button type="button" aria-label="关闭预览" @click="dialog?.close()"><X :size="18" /></button></div></div>
      <div class="preview-canvas"><iframe v-if="previewOpen" :class="{ mobile }" :srcdoc="previewHtml" sandbox="allow-scripts" referrerpolicy="no-referrer" title="生成的 HTML 报告"></iframe></div>
    </dialog>
  </div>
</template>

<style scoped>
.html-artifact{background:#fff;border:1px solid #dce4dc;border-radius:14px;padding:18px;color:#263b30;min-width:0}.artifact-top{display:flex;align-items:center;gap:12px}.artifact-top strong,.artifact-top small{display:block}.artifact-top small{font-size:12px;color:#6b7b70;margin-top:4px}.artifact-top>span{margin-left:auto;font-size:11px;color:#7a867d;white-space:nowrap}.artifact-actions{display:flex;gap:10px;margin:18px 0 12px;flex-wrap:wrap}button{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:8px 12px;border:1px solid #d7e1d6;background:white;color:#305c3a;border-radius:7px;font:inherit;font-size:13px;cursor:pointer}button:disabled{cursor:default;opacity:.5}.artifact-actions button:first-child{background:#345c36;color:white;border-color:#345c36}summary{font-size:12px;cursor:pointer;color:#6d7b70}pre{max-height:260px;overflow:auto;white-space:pre-wrap;word-break:break-all;font-size:11px;line-height:1.5;padding:12px;background:#f5f7f4}.artifact-dialog{padding:0;border:1px solid #dce4dc;border-radius:12px;width:calc(100vw - 48px);max-width:1600px;height:calc(100dvh - 48px);max-height:100dvh;overflow:hidden}.artifact-dialog::backdrop{background:#13201799}.preview-toolbar{height:54px;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 14px;background:#fff;border-bottom:1px solid #ddd}.preview-toolbar>div{display:flex;gap:6px}.preview-toolbar button[aria-pressed="true"]{background:#e7ede3}.preview-canvas{height:calc(100% - 54px);background:#e4e8e3;display:flex;justify-content:center}iframe{border:0;width:100%;height:100%;background:white}iframe.mobile{max-width:390px}@media(max-width:600px){.artifact-dialog{width:calc(100vw - 12px);height:calc(100dvh - 12px)}.html-artifact{padding:12px}.artifact-top>span{display:none}}
</style>
