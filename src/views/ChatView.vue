<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import {
  ArrowDown,
  ArrowRight,
  Bot,
  Check,
  Copy,
  MessageSquare,
  Pencil,
  Plus,
  RotateCcw,
  Send,
  Sparkles,
  Square,
  Trash2,
  User,
} from 'lucide-vue-next'
import { useChatStore, type DisplayMessage } from '../stores/chat'
import ChatTemplatePicker from '../components/ChatTemplatePicker.vue'
import HtmlArtifact from '../components/HtmlArtifact.vue'

const chat = useChatStore()
const { messages, isStreaming, errorMessage, ready, storageWarning, sortedSessions, activeSessionId, activeSession, generationStatus } = storeToRefs(chat)
const { stopGenerating, regenerate } = chat
void chat.initialize()
const input = ref('')
const messagesContainer = ref<HTMLDivElement>()
const showScrollBottom = ref(false)

const promptCards = [
  {
    title: '深度解析 RAG 架构',
    desc: '分析检索增强生成在企业落地中的关键挑战与优化策略',
    prompt: '请深入分析 RAG（检索增强生成）在企业落地中的核心挑战，以及针对召回准确率、切块粒度和重排机制的优化建议。',
  },
  {
    title: '长上下文 vs 向量检索',
    desc: '探讨百万级窗口时代两者的取舍与混合路由机制',
    prompt: '随着大模型上下文窗口迈入 1M+ 时代，企业知识库应该选择长上下文还是传统的 RAG 向量检索？请从成本、延迟和准确率进行对比。',
  },
  {
    title: '编写 Python 异步 SSE 流式客户端',
    desc: '使用 httpx 实现健壮的 Server-Sent Events 流式消费示例',
    prompt: '请用 Python 编写一段使用 httpx.AsyncClient 消费大模型 SSE 流式输出的代码，要求处理换行分包、异常重试和优雅中断。',
  },
  {
    title: '设计 AI 调研报告的视觉结构',
    desc: '如何用数据密度、图解与可核实引用打造高质量研报',
    prompt: '如何设计一份兼具专业感与高信息密度的 AI 调研报告？请给出章节编排、证据溯源机制与可视化图表的构建原则。',
  },
]

const canSend = computed(() => ready.value && input.value.trim().length > 0 && !isStreaming.value)

function scrollToBottom(smooth = true) {
  if (!messagesContainer.value) return
  messagesContainer.value.scrollTo({
    top: messages.value.length ? messagesContainer.value.scrollHeight : 0,
    behavior: smooth ? 'smooth' : 'auto',
  })
}

function handleScroll() {
  if (!messagesContainer.value) return
  const { scrollTop, scrollHeight, clientHeight } = messagesContainer.value
  const distance = scrollHeight - scrollTop - clientHeight
  showScrollBottom.value = distance > 120
}

function send(customText?: string) {
  const text = (customText ?? input.value).trim()
  if (!ready.value || !text || isStreaming.value) return
  input.value = ''
  showScrollBottom.value = false
  void chat.send(text)
}

function renameSession(id: string, title: string) {
  const updated = window.prompt('对话名称', title)
  if (updated !== null) chat.renameSession(id, updated)
}

function deleteSession(id: string) {
  if (window.confirm('确定删除这个对话及其全部消息？此操作无法撤销。')) void chat.removeSession(id)
}

async function copyText(msg: DisplayMessage) {
  try {
    await navigator.clipboard.writeText(msg.content)
    msg.copied = true
    setTimeout(() => {
      msg.copied = false
    }, 2000)
  } catch {
    // 降级使用传统模式
  }
}

function clearChat() {
  if (window.confirm('确定清空当前对话的全部消息？')) chat.clearChat()
}

watch(activeSessionId, () => {
  input.value = ''
  showScrollBottom.value = false
  void nextTick(() => scrollToBottom(false))
})

function handleKeydown(e: KeyboardEvent) {
  if (e.isComposing) return
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    void send()
  }
}

watch(messages, () => {
  nextTick(() => {
    if (!showScrollBottom.value) {
      scrollToBottom(true)
    }
  })
}, { deep: true })

onBeforeUnmount(() => {
  stopGenerating()
})
</script>

<template>
  <main class="chat-layout">
    <aside class="session-sidebar" aria-label="历史对话">
      <div class="session-heading"><strong>对话</strong><span>{{ sortedSessions.length }}</span></div>
      <button type="button" class="new-chat-btn" :disabled="!ready" @click="chat.createSession"><Plus :size="16" />新建对话</button>
      <p class="storage-note">历史记录保存在当前浏览器</p>
      <div class="session-list">
        <div v-for="session in sortedSessions" :key="session.id" class="session-item" :class="{ active: session.id === activeSessionId }">
          <button type="button" class="session-select" :aria-current="session.id === activeSessionId ? 'true' : undefined" @click="chat.selectSession(session.id)">
            <MessageSquare :size="15" />
            <span><strong>{{ session.title }}</strong><small>{{ session.messages.filter(message => message.role === 'user').length }} 轮对话 · {{ new Date(session.updatedAt).toLocaleDateString('zh-CN') }}</small></span>
          </button>
          <div class="session-tools">
            <button type="button" class="icon-btn" :aria-label="`重命名对话：${session.title}`" @click="renameSession(session.id, session.title)"><Pencil :size="13" /></button>
            <button type="button" class="icon-btn" :aria-label="`删除对话：${session.title}`" @click="deleteSession(session.id)"><Trash2 :size="13" /></button>
          </div>
        </div>
      </div>
    </aside>
    <section class="chat-workspace" aria-label="当前对话" :aria-busy="!ready">
    <!-- 头部信息栏 -->
    <header class="chat-header">
      <div class="header-info">
        <div class="model-badge">
          <span class="status-pulse"></span>
          <Bot :size="15" />
          <strong>Kimi K3</strong>
          <span class="provider-tag">OpenRouter · SSE</span>
        </div>
        <p class="header-desc">{{ activeSession?.title ?? '正在加载对话…' }}</p>
      </div>
      <div class="header-actions">
        <button
          v-if="messages.length"
          type="button"
          class="action-pill-btn"
          title="清空对话"
          @click="clearChat"
        >
          <Trash2 :size="14" />清空记录
        </button>
      </div>
    </header>

    <div v-if="storageWarning" class="chat-alert" role="alert">{{ storageWarning }}</div>
    <!-- 错误警告条 -->
    <div v-if="errorMessage" class="chat-alert" role="alert">
      <span>{{ errorMessage }}</span>
      <button type="button" class="text-button" @click="errorMessage = ''">忽略</button>
    </div>

    <!-- 消息对话区 -->
    <div
      ref="messagesContainer"
      class="chat-scroll-area"
      @scroll="handleScroll"
    >
      <ChatTemplatePicker v-if="ready" />
      <!-- 空状态欢迎页 -->
      <div v-if="!ready" class="chat-welcome" role="status">正在加载历史对话…</div>
      <div v-else-if="!messages.length" class="chat-welcome">
        <div class="welcome-icon">
          <Sparkles :size="32" />
        </div>
        <h2>{{ activeSession?.templateId ? '把报告内容交给 Kimi' : '今天想聊点什么？' }}</h2>
        <p>{{ activeSession?.templateId ? '在下方粘贴正文、提纲或研究材料，生成所选风格的交互网页。' : '选择上方模板制作报告，或直接开始普通对话。' }}</p>

        <div v-if="!activeSession?.templateId" class="quick-prompts-grid">
          <button
            v-for="card in promptCards"
            :key="card.title"
            type="button"
            class="prompt-card"
            @click="send(card.prompt)"
          >
            <div class="prompt-title">
              <span>{{ card.title }}</span>
              <ArrowRight :size="14" />
            </div>
            <p>{{ card.desc }}</p>
          </button>
        </div>
      </div>

      <!-- 消息列表 -->
      <div v-else class="messages-list">
        <div
          v-for="msg in messages"
          :key="msg.id"
          class="message-row"
          :class="msg.role"
        >
          <!-- 头像 -->
          <div class="avatar-bubble" :class="msg.role">
            <User v-if="msg.role === 'user'" :size="16" />
            <Bot v-else :size="16" />
          </div>

          <!-- 内容主体 -->
          <div class="message-bubble">
            <div class="message-meta">
              <span class="sender-name">{{ msg.role === 'user' ? '你' : 'Kimi K3' }}</span>
              <div v-if="msg.role === 'assistant' && !msg.isStreaming" class="message-tools">
                <button
                  type="button"
                  class="icon-btn"
                  :title="msg.copied ? '已复制' : '复制回答'"
                  @click="copyText(msg)"
                >
                  <Check v-if="msg.copied" :size="13" class="text-success" />
                  <Copy v-else :size="13" />
                </button>
              </div>
            </div>

            <!-- 正文与光标 -->
            <HtmlArtifact v-if="msg.role === 'assistant' && /<!doctype\s+html|<html\b/i.test(msg.content)" :content="msg.content" :streaming="msg.isStreaming" />
            <div v-else class="message-text">
                <span class="text-content">{{ msg.content || (msg.role === 'assistant' && !msg.isStreaming ? '本次未生成正文，请重新生成。' : '') }}</span>
              <span
                v-if="msg.isStreaming"
                class="typewriter-cursor"
                aria-hidden="true"
              ></span>
            </div>
          </div>
        </div>

        <!-- 针对最新一条回答的重试选项 -->
        <div
          v-if="!isStreaming && messages.length && messages[messages.length - 1].role === 'assistant'"
          class="regenerate-row"
        >
          <button type="button" class="regen-btn" @click="regenerate">
            <RotateCcw :size="13" />重新生成
          </button>
        </div>
      </div>
    </div>

    <!-- 浮动回到底部按钮 -->
    <button
      v-if="showScrollBottom"
      type="button"
      class="scroll-bottom-fab"
      aria-label="回到底部"
      @click="scrollToBottom(true)"
    >
      <ArrowDown :size="16" />
    </button>

    <!-- 底部输入框 -->
    <footer class="chat-composer-footer">
      <p v-if="generationStatus" class="generation-status" role="status">{{ generationStatus }}</p>
      <div class="composer-container">
        <textarea
          v-model="input"
          class="chat-textarea"
          :rows="activeSession?.templateId ? 4 : 1"
          :placeholder="activeSession?.templateId ? '粘贴报告正文、提纲或材料，生成这个风格的网页…' : '给 Kimi K3 发送消息... (Enter 发送，Shift + Enter 换行)'"
          maxlength="60000"
          :disabled="!ready || isStreaming"
          aria-label="对话消息"
          @keydown="handleKeydown"
        ></textarea>

        <div class="composer-actions">
          <button
            v-if="isStreaming"
            type="button"
            class="stop-btn"
            title="停止生成"
            @click="stopGenerating"
          >
            <Square :size="13" />停止生成
          </button>
          <button
            v-else
            type="button"
            class="send-btn"
            :disabled="!canSend"
            title="发送消息"
            @click="send()"
          >
            <Send :size="16" />
          </button>
        </div>
      </div>
      <div class="composer-caption">
        <span>按 Enter 发送</span>
        <span>·</span>
        <span>大模型生成内容仅供参考</span>
      </div>
    </footer>
    </section>
  </main>
</template>

<style scoped>
.generation-status { font-size: 12px; color: #6b8456; margin: 0 0 8px; }
.chat-layout {
  display: flex;
  height: calc(100dvh - 65px);
  min-height: 480px;
  min-width: 0;
}
.session-sidebar {
  width: 230px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 20px 12px;
  border-right: 1px solid #e2e8f0;
  background: #fff;
  min-height: 0;
}
.session-heading { display: flex; justify-content: space-between; color: #334155; padding: 0 8px 14px; }
.session-heading span, .storage-note { font-size: 12px; color: #64748b; }
.storage-note { margin: 12px 8px; }
.new-chat-btn { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 10px; border: 1px solid #bae6fd; border-radius: 8px; background: #f0f9ff; color: #0369a1; cursor: pointer; }
.session-list { overflow-y: auto; min-height: 0; }
.session-item { border-radius: 8px; margin-bottom: 6px; border: 1px solid transparent; }
.session-item.active { background: #f0f9ff; border-color: #bae6fd; }
.session-select { display: flex; align-items: center; gap: 8px; width: 100%; padding: 10px 8px 5px; background: transparent; border: 0; cursor: pointer; text-align: left; color: #334155; }
.session-select > svg { flex-shrink: 0; }
.session-select > span { min-width: 0; }
.session-select strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 500; }
.session-select small { display: block; color: #64748b; font-size: 11px; margin-top: 4px; }
.session-tools { display: flex; justify-content: flex-end; padding: 0 6px 6px; gap: 4px; }
.chat-workspace {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  min-height: 0;
  position: relative;
  background: #f8fafc;
}

/* 顶部信息条 */
.chat-header {
  padding: 14px 28px;
  background: #ffffff;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.model-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: #f1f5f9;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 13px;
  color: #1e293b;
}

.status-pulse {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.2);
}

.provider-tag {
  font-size: 11px;
  color: #64748b;
  border-left: 1px solid #cbd5e1;
  padding-left: 8px;
}

.header-desc {
  font-size: 12px;
  color: #64748b;
  margin: 4px 0 0;
}

.action-pill-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #64748b;
  background: transparent;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 6px 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.action-pill-btn:hover {
  background: #f1f5f9;
  color: #0f172a;
  border-color: #94a3b8;
}

/* 警示条 */
.chat-alert {
  background: #fef2f2;
  border-bottom: 1px solid #fecaca;
  color: #b91c1c;
  padding: 8px 28px;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

/* 消息滚动区 */
.chat-scroll-area {
  flex: 1;
  overflow-y: auto;
  padding: 30px 4vw;
  scroll-behavior: smooth;
}

/* 欢迎页 */
.chat-welcome {
  max-width: 680px;
  margin: 40px auto 0;
  text-align: center;
}

.welcome-icon {
  width: 58px;
  height: 58px;
  border-radius: 16px;
  background: linear-gradient(135deg, #e0f2fe, #dbeafe);
  color: #0284c7;
  display: grid;
  place-items: center;
  margin: 0 auto 16px;
  box-shadow: 0 4px 12px rgba(2, 132, 199, 0.12);
}

.chat-welcome h2 {
  font-size: 24px;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 8px;
}

.chat-welcome p {
  font-size: 14px;
  color: #64748b;
  margin: 0 0 32px;
}

.quick-prompts-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
  text-align: left;
}

.prompt-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 16px;
  cursor: pointer;
  transition: all 0.18s ease;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.prompt-card:hover {
  border-color: #38bdf8;
  box-shadow: 0 4px 14px rgba(2, 132, 199, 0.08);
  transform: translateY(-2px);
}

.prompt-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
}

.prompt-card p {
  font-size: 12px;
  color: #64748b;
  margin: 0;
  line-height: 1.5;
}

/* 消息列表 */
.messages-list {
  max-width: 860px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.message-row {
  display: flex;
  gap: 14px;
}

.message-row.user {
  flex-direction: row-reverse;
}

.avatar-bubble {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  margin-top: 2px;
}

.avatar-bubble.user {
  background: #0284c7;
  color: #ffffff;
}

.avatar-bubble.assistant {
  background: #f1f5f9;
  border: 1px solid #cbd5e1;
  color: #0f172a;
}

.message-bubble {
  max-width: calc(100% - 70px);
  display: flex;
  flex-direction: column;
}

.message-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 5px;
  padding: 0 4px;
}

.message-row.user .message-meta {
  justify-content: flex-end;
}

.sender-name {
  font-size: 12px;
  color: #64748b;
  font-weight: 500;
}

.message-tools {
  opacity: 0.7;
}

.message-bubble:hover .message-tools {
  opacity: 1;
}

.icon-btn {
  background: transparent;
  border: none;
  color: #64748b;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
  display: grid;
  place-items: center;
}

.icon-btn:hover {
  background: #e2e8f0;
  color: #0f172a;
}

.text-success {
  color: #10b981;
}

.message-text {
  padding: 14px 18px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-row.user .message-text {
  background: #0284c7;
  color: #ffffff;
  border-bottom-right-radius: 3px;
}

.message-row.assistant .message-text {
  background: #ffffff;
  color: #0f172a;
  border: 1px solid #e2e8f0;
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.03);
  border-bottom-left-radius: 3px;
}

/* 打字机光标 */
.typewriter-cursor {
  display: inline-block;
  width: 2px;
  height: 1.15em;
  background-color: #0284c7;
  margin-left: 3px;
  vertical-align: -2px;
  animation: cursor-blink 0.8s infinite;
}

@keyframes cursor-blink {
  0%, 49% { opacity: 1; }
  50%, 100% { opacity: 0; }
}

.regenerate-row {
  display: flex;
  justify-content: flex-start;
  padding-left: 48px;
}

.regen-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  color: #475569;
  border-radius: 6px;
  padding: 5px 12px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.regen-btn:hover {
  background: #f8fafc;
  border-color: #0284c7;
  color: #0284c7;
}

/* 浮动吸底按钮 */
.scroll-bottom-fab {
  position: absolute;
  right: 32px;
  bottom: 110px;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  display: grid;
  place-items: center;
  cursor: pointer;
  color: #475569;
  transition: all 0.15s ease;
  z-index: 10;
}

.scroll-bottom-fab:hover {
  background: #f1f5f9;
  color: #0284c7;
  border-color: #0284c7;
}

/* 底部输入框 */
.chat-composer-footer {
  padding: 16px 4vw 20px;
  background: #ffffff;
  border-top: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.composer-container {
  max-width: 860px;
  margin: 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 10px;
  background: #f8fafc;
  border: 1px solid #cbd5e1;
  border-radius: 12px;
  padding: 10px 14px;
  transition: border-color 0.18s ease;
}

.composer-container:focus-within {
  border-color: #0284c7;
  background: #ffffff;
  box-shadow: 0 0 0 3px rgba(2, 132, 199, 0.1);
}

.chat-textarea {
  flex: 1;
  border: none;
  background: transparent;
  outline: none;
  resize: none;
  font-size: 14px;
  line-height: 1.6;
  max-height: 160px;
  color: #0f172a;
}

.composer-actions {
  display: flex;
  align-items: center;
}

.send-btn {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: #0284c7;
  color: #ffffff;
  border: none;
  display: grid;
  place-items: center;
  cursor: pointer;
  transition: all 0.15s ease;
}

.send-btn:disabled {
  background: #cbd5e1;
  cursor: not-allowed;
}

.send-btn:not(:disabled):hover {
  background: #0369a1;
}

.stop-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #fee2e2;
  color: #dc2626;
  border: 1px solid #fca5a5;
  border-radius: 8px;
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.stop-btn:hover {
  background: #fecaca;
}

.composer-caption {
  max-width: 860px;
  margin: 8px auto 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 11px;
  color: #94a3b8;
}

@media (max-width: 768px) {
  .chat-layout { flex-direction: column; height: calc(100dvh - 125px); min-height: 600px; }
  .session-sidebar { width: auto; max-height: 190px; padding: 10px 12px; border-right: 0; border-bottom: 1px solid #e2e8f0; }
  .session-heading, .storage-note { display: none; }
  .session-list { display: flex; gap: 8px; overflow-x: auto; margin-top: 8px; }
  .session-item { width: 180px; flex-shrink: 0; }
  .chat-header { padding: 12px; gap: 8px; }
  .provider-tag { display: none; }
  .chat-composer-footer { padding: 12px; }
  .chat-textarea { min-width: 0; }
  .chat-welcome { margin-top: 10px; }

  .quick-prompts-grid {
    grid-template-columns: 1fr;
  }
  .chat-scroll-area {
    padding: 16px;
  }
  .message-bubble {
    max-width: 85%;
  }
}
</style>
