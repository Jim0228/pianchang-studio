<script setup lang="ts">
import { computed } from 'vue'
import type { Generation } from '../api'

const props = defineProps<{ generation: Generation | null; title?: string }>()

const statusText = computed(() => {
  const status = props.generation?.status
  if (status === 'succeeded') return '已完成'
  if (status === 'failed') return '失败'
  if (status === 'running') return '生成中'
  return '排队中'
})

const progressStatus = computed(() => {
  if (props.generation?.status === 'failed') return 'exception'
  if (props.generation?.status === 'succeeded') return 'success'
  return undefined
})
</script>

<template>
  <div class="progress-card">
    <div class="progress-head">
      <div>
        <p class="progress-title">{{ title || '生成进度' }}</p>
        <p class="progress-msg">{{ generation?.message || '等待开始…' }}</p>
      </div>
      <span class="pill" :class="{ accent: generation?.status === 'succeeded' }">{{ statusText }}</span>
    </div>
    <el-progress
      :percentage="generation?.progress || 0"
      :status="progressStatus"
      :stroke-width="8"
      :show-text="false"
    />
    <p v-if="generation?.error" class="progress-error">{{ generation.error }}</p>
  </div>
</template>

<style scoped>
.progress-card {
  padding: 18px 20px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-2);
}

.progress-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.progress-title {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 700;
}

.progress-msg {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}

.progress-error {
  margin: 12px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--red);
  background: #fdf0ee;
  border-radius: 8px;
  padding: 8px 10px;
}
</style>
