<template>
  <div class="chat-widget">
    <div class="chat-fab" @click="toggleChat" :class="{ active: isOpen }">
      <span v-if="!isOpen">💬</span>
      <span v-else>✕</span>
      <div v-if="hasPendingQuestion && !isOpen" class="chat-badge">!</div>
    </div>

    <transition name="chat-slide">
      <div v-if="isOpen" class="chat-window">
        <div class="chat-header">
          <div class="chat-header-left">
            <button v-if="showHistory" class="chat-back-btn" @click="showHistory = false">←</button>
            <span>🤖 智能旅行助手</span>
          </div>
          <div class="chat-header-right">
            <button class="chat-history-btn" @click="toggleHistory" :class="{ active: showHistory }" title="历史记录">📋</button>
            <button class="chat-new-btn" @click="newSession" title="新对话">➕</button>
            <span class="chat-status">{{ isThinking ? '思考中...' : '在线' }}</span>
          </div>
        </div>

        <div v-if="showHistory" class="chat-history-list">
          <div v-if="sessions.length === 0" class="history-empty">暂无历史会话</div>
          <div
            v-for="s in sessions"
            :key="s.session_id"
            class="history-item"
            :class="{ active: s.session_id === sessionId }"
            @click="switchSession(s.session_id)"
          >
            <div class="history-item-info">
              <div class="history-item-title">{{ s.title }}</div>
              <div class="history-item-meta">{{ s.message_count }}条消息</div>
            </div>
            <button class="history-item-delete" @click.stop="removeSession(s.session_id)">🗑</button>
          </div>
        </div>

        <template v-else>
          <div class="chat-messages" ref="messagesRef">
            <div
              v-for="(msg, idx) in messages"
              :key="idx"
              class="chat-message"
              :class="msg.role"
            >
              <div class="msg-avatar">{{ msg.role === 'user' ? '👤' : '🤖' }}</div>
              <div class="msg-content">
                <div class="msg-text" v-html="renderMarkdown(msg.content)"></div>
                <div v-if="msg.actionLabel" class="msg-action-tag">
                  ✅ {{ msg.actionLabel }}
                </div>
              </div>
            </div>

            <div v-if="isThinking" class="chat-message assistant">
              <div class="msg-avatar">🤖</div>
              <div class="msg-content">
                <div class="thinking-dots">
                  <span></span><span></span><span></span>
                </div>
              </div>
            </div>
          </div>

          <div v-if="pendingQuestion" class="chat-pending">
            <div class="pending-text">{{ pendingQuestion }}</div>
            <div v-if="quickReplies.length" class="quick-replies">
              <button
                v-for="(reply, idx) in quickReplies"
                :key="idx"
                class="quick-reply-btn"
                @click="sendQuickReply(reply)"
              >
                {{ reply }}
              </button>
            </div>
          </div>

          <div class="chat-input">
            <input
              v-model="inputText"
              placeholder="输入消息..."
              @keydown.enter="sendMessage"
              :disabled="isThinking"
              class="chat-input-field"
            />
            <button class="chat-send-btn" @click="sendMessage" :disabled="isThinking || !inputText.trim()">
              发送
            </button>
          </div>
        </template>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, watch, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import axios from 'axios'
import { getChatSessions, getChatSession, deleteChatSession, getSessionId, setSessionId, clearSessionId, type SessionInfo } from '@/services/api'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

interface ChatAction {
  type: string
  data: Record<string, any> | null
}

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
  actionLabel?: string
}

const emit = defineEmits<{
  (e: 'fill-form', data: Record<string, any>): void
  (e: 'adjust-plan', data: { target: string; feedback: string }): void
  (e: 'navigate', data: { to: string }): void
  (e: 'generate-trip', data: Record<string, any>): void
}>()

const route = useRoute()
const currentPage = computed(() => route.path === '/result' ? 'result' : 'home')

const isOpen = ref(false)
const inputText = ref('')
const messages = ref<ChatMsg[]>([
  { role: 'assistant', content: '你好！我是智能旅行助手。你可以直接告诉我你想去哪、喜欢什么，我会帮你自动填写表单；在结果页我还能帮你AI调整行程！' }
])
const isThinking = ref(false)
const sessionId = ref('')
const pendingQuestion = ref('')
const quickReplies = ref<string[]>([])
const hasPendingQuestion = ref(false)
const messagesRef = ref<HTMLElement | null>(null)
const showHistory = ref(false)
const sessions = ref<SessionInfo[]>([])

onMounted(() => {
  const sid = getSessionId()
  if (sid) {
    sessionId.value = sid
    loadSessionHistory(sid)
  }
})

async function loadSessionHistory(sid: string) {
  const detail = await getChatSession(sid)
  if (detail && detail.messages.length > 0) {
    messages.value = detail.messages.map(m => ({
      role: m.role as 'user' | 'assistant',
      content: m.content,
    }))
    scrollToBottom()
  }
}

async function toggleHistory() {
  showHistory.value = !showHistory.value
  if (showHistory.value) {
    sessions.value = await getChatSessions()
  }
}

async function switchSession(sid: string) {
  sessionId.value = sid
  setSessionId(sid)
  await loadSessionHistory(sid)
  showHistory.value = false
}

async function removeSession(sid: string) {
  await deleteChatSession(sid)
  sessions.value = sessions.value.filter(s => s.session_id !== sid)
  if (sid === sessionId.value) {
    newSession()
  }
}

function newSession() {
  sessionId.value = ''
  clearSessionId()
  messages.value = [
    { role: 'assistant', content: '你好！我是智能旅行助手。你可以直接告诉我你想去哪、喜欢什么，我会帮你自动填写表单；在结果页我还能帮你AI调整行程！' }
  ]
  showHistory.value = false
}

function toggleChat() {
  isOpen.value = !isOpen.value
}

function renderMarkdown(text: string): string {
  return text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`(.*?)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br/>')
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

watch(messages, scrollToBottom, { deep: true })

function getActionLabel(action: ChatAction): string {
  if (action.type === 'fill_form') return '已自动填写表单'
  if (action.type === 'navigate') return '正在跳转...'
  if (action.type === 'adjust_plan') return '正在AI调整行程...'
  if (action.type === 'generate_trip') return '正在生成行程...'
  return ''
}

function handleAction(action: ChatAction) {
  if (!action.data) return

  if (action.type === 'fill_form') {
    emit('fill-form', action.data)
  } else if (action.type === 'navigate') {
    if (action.data.navigate_to === 'result') {
      emit('navigate', { to: 'result' })
    }
  } else if (action.type === 'adjust_plan') {
    const target = action.data.target || '整体'
    const feedback = action.data.feedback || ''
    if (feedback) {
      emit('adjust-plan', { target, feedback })
    }
  } else if (action.type === 'generate_trip') {
    const tripParams = action.data.trip_params || {}
    emit('generate-trip', tripParams)
  }
}

function getContext(): Record<string, any> {
  if (currentPage.value === 'result') {
    const planStr = localStorage.getItem('tripPlan')
    if (planStr) {
      try {
        const plan = JSON.parse(planStr)
        return {
          city: plan.city,
          start_date: plan.start_date,
          end_date: plan.end_date,
          days_count: plan.days?.length,
          days_summary: plan.days?.map((d: any) => ({
            day: d.day_index + 1,
            attractions: d.attractions?.map((a: any) => a.name),
            hotel: d.hotel?.name,
          })),
        }
      } catch { /* ignore */ }
    }
  }
  return {}
}

async function sendMessage() {
  const text = inputText.value.trim()
  if (!text || isThinking.value) return

  messages.value.push({ role: 'user', content: text })
  inputText.value = ''
  isThinking.value = true
  pendingQuestion.value = ''
  quickReplies.value = []
  hasPendingQuestion.value = false

  try {
    const res = await axios.post(`${API_BASE}/api/chat/message`, {
      session_id: sessionId.value || null,
      message: text,
      context: getContext(),
      history: messages.value.slice(-10).map(m => ({ role: m.role, content: m.content })),
      page: currentPage.value,
    })

    sessionId.value = res.data.session_id
    setSessionId(res.data.session_id)

    const action: ChatAction | null = res.data.action
    let actionLabel = ''

    if (action && action.type && action.type !== 'none') {
      actionLabel = getActionLabel(action)
    }

    messages.value.push({
      role: 'assistant',
      content: res.data.reply,
      actionLabel,
    })

    if (action && action.type && action.type !== 'none') {
      handleAction(action)
    }

    if (res.data.need_user_input && res.data.pending_question) {
      pendingQuestion.value = res.data.pending_question
      quickReplies.value = res.data.quick_replies || []
      hasPendingQuestion.value = true
    }
  } catch (e: any) {
    messages.value.push({ role: 'assistant', content: '抱歉，请求出错了，请稍后再试。' })
  } finally {
    isThinking.value = false
    nextTick(() => { inputText.value = '' })
  }
}

function sendQuickReply(reply: string) {
  inputText.value = reply
  sendMessage()
}

async function checkInteraction(formData: any) {
  try {
    const res = await axios.post(`${API_BASE}/api/chat/check-interaction`, formData)
    if (res.data.need_user_input) {
      pendingQuestion.value = res.data.pending_question
      quickReplies.value = res.data.quick_replies || []
      hasPendingQuestion.value = true
      if (!isOpen.value) {
        isOpen.value = true
      }
    }
  } catch (e) {
    console.error('交互检查失败:', e)
  }
}

defineExpose({ checkInteraction, pendingQuestion, quickReplies })
</script>

<style scoped>
.chat-widget {
  position: fixed;
  bottom: 24px;
  right: 24px;
  z-index: 9999;
}

.chat-fab {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: linear-gradient(135deg, #1890ff, #36cfc9);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(24, 144, 255, 0.4);
  transition: all 0.3s;
  position: relative;
}

.chat-fab:hover {
  transform: scale(1.1);
  box-shadow: 0 6px 20px rgba(24, 144, 255, 0.5);
}

.chat-fab.active {
  background: linear-gradient(135deg, #ff4d4f, #ff7875);
}

.chat-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #ff4d4f;
  color: white;
  font-size: 12px;
  font-weight: bold;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.2); }
}

.chat-window {
  position: fixed;
  bottom: 92px;
  right: 24px;
  width: 400px;
  height: 560px;
  background: white;
  border-radius: 16px;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-header {
  padding: 14px 20px;
  background: linear-gradient(135deg, #1890ff, #36cfc9);
  color: white;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  flex-shrink: 0;
}

.chat-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chat-header-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.chat-back-btn,
.chat-history-btn,
.chat-new-btn {
  background: rgba(255, 255, 255, 0.2);
  border: none;
  color: white;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s;
}

.chat-back-btn:hover,
.chat-history-btn:hover,
.chat-new-btn:hover {
  background: rgba(255, 255, 255, 0.35);
}

.chat-history-btn.active {
  background: rgba(255, 255, 255, 0.4);
}

.chat-history-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  background: #f5f7fa;
}

.history-empty {
  text-align: center;
  color: #999;
  padding: 40px 0;
  font-size: 14px;
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s;
  margin-bottom: 4px;
}

.history-item:hover {
  background: #e6f7ff;
}

.history-item.active {
  background: #bae7ff;
}

.history-item-info {
  flex: 1;
  min-width: 0;
}

.history-item-title {
  font-size: 14px;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.history-item-meta {
  font-size: 12px;
  color: #999;
  margin-top: 2px;
}

.history-item-delete {
  background: none;
  border: none;
  font-size: 14px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.2s;
  padding: 4px;
}

.history-item:hover .history-item-delete {
  opacity: 0.6;
}

.history-item-delete:hover {
  opacity: 1 !important;
}

.chat-status {
  font-size: 12px;
  opacity: 0.8;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: #f5f7fa;
}

.chat-message {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.chat-message.user {
  flex-direction: row-reverse;
}

.msg-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
  background: white;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.msg-content {
  max-width: 75%;
}

.msg-text {
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}

.chat-message.assistant .msg-text {
  background: white;
  color: #333;
  border-bottom-left-radius: 4px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}

.chat-message.assistant .msg-text :deep(strong) {
  color: #1890ff;
}

.chat-message.assistant .msg-text :deep(code) {
  background: #f0f0f0;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 13px;
}

.chat-message.user .msg-text {
  background: linear-gradient(135deg, #1890ff, #36cfc9);
  color: white;
  border-bottom-right-radius: 4px;
}

.msg-action-tag {
  margin-top: 6px;
  padding: 4px 10px;
  background: #e6f7ff;
  border: 1px solid #91d5ff;
  border-radius: 10px;
  font-size: 12px;
  color: #1890ff;
  display: inline-block;
}

.thinking-dots {
  display: flex;
  gap: 4px;
  padding: 10px 14px;
  background: white;
  border-radius: 12px;
  border-bottom-left-radius: 4px;
  width: fit-content;
}

.thinking-dots span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #1890ff;
  animation: bounce 1.4s infinite;
}

.thinking-dots span:nth-child(2) { animation-delay: 0.2s; }
.thinking-dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

.chat-pending {
  padding: 12px 16px;
  background: #fff7e6;
  border-top: 1px solid #ffe58f;
  flex-shrink: 0;
}

.pending-text {
  font-size: 13px;
  color: #ad6800;
  margin-bottom: 8px;
}

.quick-replies {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.quick-reply-btn {
  padding: 4px 12px;
  border-radius: 14px;
  border: 1px solid #1890ff;
  background: white;
  color: #1890ff;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.quick-reply-btn:hover {
  background: #1890ff;
  color: white;
}

.chat-input {
  padding: 12px 16px;
  border-top: 1px solid #f0f0f0;
  display: flex;
  gap: 8px;
  background: white;
  flex-shrink: 0;
}

.chat-input-field {
  flex: 1;
  padding: 6px 12px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  font-size: 14px;
  outline: none;
  transition: border-color 0.2s;
}

.chat-input-field:focus {
  border-color: #1890ff;
  box-shadow: 0 0 0 2px rgba(24, 144, 255, 0.1);
}

.chat-input-field:disabled {
  background: #f5f5f5;
  cursor: not-allowed;
}

.chat-send-btn {
  padding: 6px 16px;
  border-radius: 6px;
  border: none;
  background: linear-gradient(135deg, #1890ff, #36cfc9);
  color: white;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.chat-send-btn:hover:not(:disabled) {
  opacity: 0.9;
}

.chat-send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.chat-slide-enter-active,
.chat-slide-leave-active {
  transition: all 0.3s ease;
}

.chat-slide-enter-from,
.chat-slide-leave-to {
  opacity: 0;
  transform: translateY(20px) scale(0.95);
}
</style>
