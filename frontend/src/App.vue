<template>
  <div id="app">
    <a-layout style="min-height: 100vh">
      <a-layout-header style="background: #001529; padding: 0 50px">
        <div style="color: white; font-size: 24px; font-weight: bold">
          🌍 Langchain智能旅行助手
        </div>
      </a-layout-header>
      <a-layout-content style="padding: 24px">
        <router-view />
      </a-layout-content>
      <a-layout-footer style="text-align: center">
        Langchain智能旅行助手 ©2026 基于langchain框架
      </a-layout-footer>
    </a-layout>
    <ChatWidget
      @fill-form="onFillForm"
      @adjust-plan="onAdjustPlan"
      @navigate="onNavigate"
      @generate-trip="onGenerateTrip"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, provide } from 'vue'
import { useRouter } from 'vue-router'
import ChatWidget from '@/components/ChatWidget.vue'

const router = useRouter()

const chatActionHandlers = ref<{
  fillForm: ((data: Record<string, any>) => void) | null
  adjustPlan: ((data: { target: string; feedback: string }) => void) | null
  generateTrip: ((data: Record<string, any>) => void) | null
}>({
  fillForm: null,
  adjustPlan: null,
  generateTrip: null,
})

provide('chatActionHandlers', chatActionHandlers)

function onFillForm(data: Record<string, any>) {
  if (chatActionHandlers.value.fillForm) {
    chatActionHandlers.value.fillForm(data)
  }
}

function onAdjustPlan(data: { target: string; feedback: string }) {
  if (chatActionHandlers.value.adjustPlan) {
    chatActionHandlers.value.adjustPlan(data)
  }
}

function onGenerateTrip(data: Record<string, any>) {
  if (chatActionHandlers.value.generateTrip) {
    chatActionHandlers.value.generateTrip(data)
  }
}

function onNavigate(data: { to: string }) {
  if (data.to === 'result') {
    router.push('/result')
  }
}
</script>

<style>
#app {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
    'Noto Sans', sans-serif;
}
</style>
