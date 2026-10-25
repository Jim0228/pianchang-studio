<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { api, formatDate, type Generation } from '../api'
import { generationStatus, stageLabel } from '../ui'

const router = useRouter()
const rows = ref<Generation[]>([])
const loading = ref(true)
let timer: number | undefined

async function load() {
  try {
    rows.value = await api.tasks()
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 3000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})

function openProject(row: Generation) {
  if (row.project_id) router.push(`/projects/${row.project_id}`)
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <p class="eyebrow">TASK CENTER</p>
        <h1 class="page-title">任务中心</h1>
        <p class="page-sub">关键帧、成片与脚本任务都在这里。任务在后台执行，离开页面不会中断。</p>
      </div>
      <button class="btn" type="button" @click="load">
        <el-icon><Refresh /></el-icon>
        刷新
      </button>
    </div>

    <div class="card table-card">
      <el-table
        v-loading="loading"
        :data="rows"
        style="width: 100%"
        row-key="id"
        @row-click="openProject"
      >
        <el-table-column label="项目" min-width="180">
          <template #default="{ row }">
            <strong>{{ row.project_name || '—' }}</strong>
            <div class="mono muted">{{ row.id }}</div>
          </template>
        </el-table-column>
        <el-table-column label="阶段" width="120">
          <template #default="{ row }">{{ stageLabel(row.stage) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <span class="status" :class="generationStatus(row.status).tone">
              {{ generationStatus(row.status).label }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="进度" min-width="190">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress"
              :status="row.status === 'failed' ? 'exception' : row.status === 'succeeded' ? 'success' : undefined"
              :stroke-width="7"
            />
          </template>
        </el-table-column>
        <el-table-column label="信息" min-width="220">
          <template #default="{ row }">
            <span v-if="row.error" class="error-text">{{ row.error }}</span>
            <span v-else class="muted">{{ row.message || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="140">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <div v-if="!loading && !rows.length" class="empty">还没有任务。新建视频后，任务会出现在这里。</div>
    </div>
  </div>
</template>

<style scoped>
.table-card {
  padding: 10px 14px 14px;
  overflow: hidden;
}

.status {
  display: inline-flex;
  padding: 3px 8px;
  border-radius: 999px;
  background: #f1ece3;
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
}

.status.success {
  background: #e6f5ed;
  color: var(--green);
}

.status.warning {
  background: #fdf3df;
  color: var(--amber);
}

.status.danger {
  background: #fdecea;
  color: var(--red);
}

.error-text {
  color: var(--red);
  font-size: 12px;
}

:deep(.el-table) {
  --el-table-border-color: var(--line);
  --el-table-header-bg-color: var(--surface-2);
  --el-table-row-hover-bg-color: #fbf7f0;
  cursor: pointer;
}

:deep(.el-table th.el-table__cell) {
  font-size: 11px;
  letter-spacing: 1px;
  color: var(--muted);
  font-weight: 600;
}
</style>
