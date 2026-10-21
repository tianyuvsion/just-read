<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { Check, ChevronDown, LayoutTemplate } from 'lucide-vue-next'
import { useChatStore } from '../stores/chat'

const chat = useChatStore()
const { templates, templateError, activeSession, isStreaming, ready } = storeToRefs(chat)
const expanded = ref(true)
const query = ref('')
const filteredTemplates = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase()
  return templates.value.filter(item => `${item.name} ${item.description} ${item.tags.join(' ')}`.toLocaleLowerCase().includes(keyword))
})
const selected = computed(() => templates.value.find(item => item.id === activeSession.value?.templateId))
const prompt = computed({ get: () => activeSession.value?.templatePrompt ?? '', set: value => chat.updateTemplate(value, activeSession.value?.templateReference ?? true) })
const reference = computed({ get: () => activeSession.value?.templateReference ?? true, set: value => chat.updateTemplate(prompt.value, value) })
const showCards = computed(() => expanded.value)
watch(() => activeSession.value?.id, () => { expanded.value = !activeSession.value?.templateId && !activeSession.value?.messages.length; query.value = '' }, { immediate: true })
function choose(id: string) { chat.chooseTemplate(id); expanded.value = false }
</script>

<template>
  <section class="template-picker" aria-label="报告模板">
    <div class="template-heading"><div><LayoutTemplate :size="17" /><strong>{{ selected ? selected.name : '选择报告模板' }}</strong><span>{{ selected ? '把你的内容生成这个风格的网页' : '选一种风格，再输入报告内容' }}</span></div><button type="button" class="toggle" :disabled="isStreaming || !ready" :aria-expanded="showCards" @click="expanded = !expanded"><span>{{ selected ? '更换模板' : showCards ? '收起' : '展开' }}</span><ChevronDown :size="14" /></button></div>
    <p v-if="templateError" class="template-error" role="alert">{{ templateError }} <button type="button" @click="chat.refreshTemplates">重试</button></p>
    <div v-if="showCards && templates.length" class="template-search"><input v-model="query" type="search" aria-label="搜索报告模板" placeholder="搜索风格或用途，例如：论文、商业、科普" /><span>{{ filteredTemplates.length }} / {{ templates.length }} 个模板</span></div>
    <div v-if="showCards" class="template-grid">
      <button v-for="item in filteredTemplates" :key="item.id" type="button" class="template-card" :class="{ selected: selected?.id === item.id }" :disabled="isStreaming || !ready" :aria-pressed="selected?.id === item.id" :aria-label="`选择模板：${item.name}`" @click="choose(item.id)">
        <div class="thumbnail" :style="{ background: item.palette[0] }"><img :src="item.preview_url" :alt="`${item.name}案例预览`" loading="lazy" /><span v-if="selected?.id === item.id" class="chosen"><Check :size="14" /></span></div>
        <div class="template-copy"><strong>{{ item.name }}</strong><p>{{ item.description }}</p><div class="tags"><span v-for="tag in item.tags" :key="tag">{{ tag }}</span></div></div>
      </button>
      <p v-if="!templates.length && !templateError" class="loading">正在加载模板…</p>
      <p v-else-if="templates.length && !filteredTemplates.length" class="loading">没有匹配的模板，请换个关键词。</p>
    </div>
    <div v-if="selected" class="template-settings">
      <div class="template-mode"><label><input v-model="reference" type="checkbox" :disabled="isStreaming" />附带原案例源码</label><span>{{ reference ? '沿用布局与交互，替换成你的报告内容' : '仅使用下方风格提示词生成，不发送源码' }}</span><button type="button" :disabled="isStreaming" @click="choose('')">切回普通对话</button></div>
      <details><summary>查看或修改默认提示词</summary><textarea v-model="prompt" aria-label="模板提示词" :disabled="isStreaming" maxlength="12000" rows="9"></textarea><button type="button" :disabled="isStreaming" @click="chat.updateTemplate(selected.default_prompt, reference)">恢复默认提示词</button></details>
    </div>
  </section>
</template>

<style scoped>
.template-mode input{accent-color:var(--primary)}
.template-search{display:flex;gap:12px;align-items:center;margin-top:14px}.template-search input{min-width:0;flex:1;border:1px solid #e2e8f0;border-radius:7px;padding:9px 10px;font:inherit;font-size:12px;background:#f8fafc}.template-search span{white-space:nowrap;font-size:11px;color:#64748b}
.template-picker{max-width:1000px;margin:0 auto 24px;padding:18px;background:#fff;border:1px solid #e2e8f0;border-radius:14px}.template-heading,.template-heading>div{display:flex;gap:8px;align-items:center;min-width:0}.template-heading{justify-content:space-between}.template-heading strong{font-size:14px;color:#1e293b}.template-heading span{font-size:12px;color:#64748b}.toggle{display:flex;align-items:center;gap:4px;white-space:nowrap}.template-picker button{font:inherit;cursor:pointer}.template-picker button:disabled{opacity:.55;cursor:default}.toggle,.template-settings button,.template-error button{border:0;background:transparent;color:var(--primary);font-size:12px;padding:4px}.template-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:16px}.template-card{text-align:left;padding:0;overflow:hidden;background:#fff;border:1px solid #e2e8f0;border-radius:10px;transition:box-shadow .2s,border-color .2s}.template-card:hover,.template-card.selected{border-color:var(--primary);box-shadow:0 3px 14px #1677ff16}.thumbnail{position:relative;aspect-ratio:1.6;overflow:hidden}.thumbnail img{width:100%;height:100%;object-fit:cover;object-position:top;display:block}.chosen{position:absolute;right:8px;top:8px;background:var(--primary);color:#fff;display:grid;place-items:center;width:24px;height:24px;border-radius:50%}.template-copy{padding:11px 12px}.template-copy strong{font-size:13px;color:#1e293b}.template-copy p{font-size:11px;line-height:1.6;color:#64748b;margin:5px 0 8px}.tags{display:flex;gap:5px}.tags span{font-size:10px;color:#0958d9;background:var(--primary-light);padding:2px 6px;border-radius:4px}.template-settings{margin-top:14px;padding-top:12px;border-top:1px solid #e2e8f0}.template-mode{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12px}.template-mode label{display:flex;align-items:center;gap:5px;color:#334155}.template-mode>span{color:#64748b;font-size:11px}.template-mode>button{margin-left:auto}.template-settings details{margin-top:10px}.template-settings summary{cursor:pointer;font-size:12px;color:#475569}.template-settings textarea{width:100%;resize:vertical;min-height:140px;max-height:400px;margin-top:10px;padding:12px;border:1px solid #cbd5e1;border-radius:8px;font-family:inherit;font-size:12px;line-height:1.8;color:#334155;background:#f8fafc}.template-error{font-size:12px;color:#b91c1c}.loading{font-size:12px;color:#64748b}@media(max-width:950px){.template-heading>div>span{display:none}.template-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:480px){.template-picker{padding:12px}.template-grid{gap:8px}.template-copy{padding:9px}.template-copy p{display:none}.tags{margin-top:5px}.template-mode>span{width:100%}.template-mode>button{margin-left:0}.template-heading strong{font-size:13px}}
</style>
