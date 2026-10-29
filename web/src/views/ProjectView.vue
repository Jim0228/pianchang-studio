<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, formatDate, mediaUrl, type ProjectDetail, type Shot } from '../api'
import { generationStatus, platformLabel, projectStatus } from '../ui'
import GenerationProgress from '../components/GenerationProgress.vue'

const props = defineProps<{ id: string }>()

const detail = ref<ProjectDetail | null>(null)
const loading = ref(true)
const busy = ref(false)
const exporting = ref(false)
const regeneratingShot = ref('')
const imageVersion = ref(0)
let pollTimer: number | undefined
let lastGenStatus = ''

const firstKeyframe = computed(() => {
  const shot = detail.value?.script?.shots.find((item) => item.keyframe_path)
  return shot?.keyframe_path ? `${mediaUrl(shot.keyframe_path)}?v=${imageVersion.value}` : ''
})

const showProgress = computed(() => {
  const generation = detail.value?.latest_generation
  if (!generation) return false
  return generation.status === 'queued' || generation.status === 'running' || generation.status === 'failed'
})

const video = computed(() => detail.value?.video || null)
const script = computed(() => detail.value?.script || null)
const brief = computed(() => detail.value?.product?.brief || {})

async function load(silent = false) {
  try {
    detail.value = await api.project(props.id)
    const generation = detail.value?.latest_generation
    if (generation?.status === 'succeeded' && lastGenStatus === 'running') {
      imageVersion.value += 1
    }
    lastGenStatus = generation?.status || ''
    if (generation && generation.status !== 'running' && generation.status !== 'queued') {
      regeneratingShot.value = ''
    }
    schedulePoll()
  } catch (error) {
    if (!silent) ElMessage.error((error as Error).message)
  } finally {
    loading.value = false
  }
}

function schedulePoll() {
  if (pollTimer) window.clearTimeout(pollTimer)
  const generation = detail.value?.latest_generation
  if (generation && (generation.status === 'queued' || generation.status === 'running')) {
    pollTimer = window.setTimeout(() => load(true), 1500)
  }
}

async function startPrevis() {
  if (!script.value) return
  busy.value = true
  try {
    detail.value!.latest_generation = await api.previs(script.value.id, !!firstKeyframe.value)
    schedulePoll()
    ElMessage.success('分镜图任务已开始')
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    busy.value = false
  }
}

async function regenerateShot(shot: Shot) {
  if (!script.value) return
  regeneratingShot.value = shot.id
  try {
    detail.value!.latest_generation = await api.regenerateShot(shot.id)
    schedulePoll()
    ElMessage.success(`#${shot.idx} 分镜任务已开始`)
  } catch (error) {
    ElMessage.error((error as Error).message)
    regeneratingShot.value = ''
  }
}

async function startRender() {
  if (!script.value) return
  busy.value = true
  try {
    detail.value!.latest_generation = await api.render(script.value.id)
    schedulePoll()
    ElMessage.success('成片任务已开始')
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    busy.value = false
  }
}

async function exportVideo() {
  if (!video.value) return
  exporting.value = true
  try {
    const result = await api.exportVideo(video.value.id)
    ElMessage.success('导出完成，开始下载')
    window.open(api.downloadUrl(result.id), '_blank')
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    exporting.value = false
  }
}

onMounted(() => load())
onUnmounted(() => {
  if (pollTimer) window.clearTimeout(pollTimer)
})
</script>

<template>
  <div class="page" v-loading="loading">
    <template v-if="detail">
      <div class="page-head">
        <div>
          <router-link to="/" class="back">← 工作台</router-link>
          <h1 class="page-title">{{ detail.project.name }}</h1>
          <div class="meta-row">
            <span class="pill">{{ platformLabel(detail.project.platform) }}</span>
            <span class="pill">{{ detail.project.duration }}s · {{ detail.project.ratio }}</span>
            <span class="pill" :class="projectStatus(detail.project.status).tone">
              {{ projectStatus(detail.project.status).label }}
            </span>
            <span class="muted small">{{ formatDate(detail.project.updated_at) }}</span>
          </div>
        </div>
        <div class="actions">
          <router-link class="btn" :to="`/projects/new?project=${detail.project.id}`">
            <el-icon><EditPen /></el-icon>
            继续编辑
          </router-link>
          <button class="btn btn-primary" type="button" :disabled="!video || exporting" @click="exportVideo">
            <el-icon><Download /></el-icon>
            {{ exporting ? '打包中…' : '导出 ZIP' }}
          </button>
        </div>
      </div>

      <GenerationProgress v-if="showProgress" :generation="detail.latest_generation" title="当前任务" />

      <div class="grid two-col content">
        <section class="player-card card">
          <video
            v-if="video"
            controls
            playsinline
            :src="video.url"
            :poster="video.cover?.url"
          />
          <div v-else class="player-empty">
            <img v-if="firstKeyframe" :src="firstKeyframe" :alt="detail.project.name" />
            <div v-else class="placeholder">
              <el-icon><Film /></el-icon>
              <span>还没有分镜图</span>
            </div>
            <span class="player-hint">
              {{ script ? '分镜图已就绪，生成成片后可在这里播放。' : '先生成脚本，再生成分镜图。' }}
            </span>
          </div>
          <div class="player-foot">
            <span v-if="video">{{ video.width }}×{{ video.height }} · {{ video.duration.toFixed(1) }}s</span>
            <span v-else>9:16 · 1080×1920</span>
            <span v-if="video?.meta?.audio?.voices?.length" class="pill">
              AI 配音 {{ video.meta.audio.voices.length }} 句
            </span>
            <span v-if="video?.meta?.audio?.bgm" class="pill">整条 BGM</span>
            <span class="ai-badge">AI 生成</span>
          </div>
        </section>

        <aside class="panel side">
          <h2 class="panel-title">商品档案</h2>
          <div v-if="detail.product" class="brief">
            <strong>{{ detail.product.title }}</strong>
            <p class="muted small">{{ detail.product.category }} · {{ detail.product.audience }}</p>
            <div class="chips">
              <span v-for="point in detail.product.selling_points" :key="point" class="tag">{{ point }}</span>
            </div>
            <p class="price" v-if="detail.product.price">建议价 ¥{{ detail.product.price }}</p>
          </div>
          <div v-else class="muted small">尚未解析商品。</div>

          <div class="divider" />

          <h3 class="side-title">进度</h3>
          <ul class="checklist">
            <li :class="{ done: !!detail.product }">商品解析</li>
            <li :class="{ done: !!script }">脚本 / 分镜</li>
            <li :class="{ done: !!firstKeyframe }">分镜图</li>
            <li :class="{ done: !!video }">成片</li>
          </ul>

          <div class="divider" />

          <div class="side-actions">
            <button
              v-if="script && !video"
              class="btn"
              type="button"
              :disabled="busy"
              @click="startPrevis"
            >
              {{ firstKeyframe ? '重新生成分镜图' : '生成分镜图' }}
            </button>
            <button
              v-if="script"
              class="btn btn-primary"
              type="button"
              :disabled="busy"
              @click="startRender"
            >
              {{ video ? '重新生成成片' : '生成成片' }}
            </button>
            <router-link v-if="!script" class="btn btn-primary" to="/projects/new">
              去生成脚本
            </router-link>
            <router-link v-if="script" class="btn" :to="`/projects/new?project=${id}`">
              编辑脚本 / 分镜
            </router-link>
            <a v-if="video" class="btn" :href="api.videoDownloadUrl(video.id)" download>
              下载 MP4
            </a>
          </div>

          <div class="divider" />

          <h3 class="side-title">合规检查</h3>
          <div v-if="script?.compliance?.length" class="compliance">
            <div v-for="(item, index) in script.compliance" :key="index" class="compliance-item">
              <span class="dot" :class="item.level" />
              <div>
                <strong>{{ item.word }}</strong>
                <p>{{ item.message }}</p>
              </div>
            </div>
          </div>
          <p v-else class="muted small">未发现明显风险词。发布前仍建议人工复核。</p>

          <div class="divider" />

          <p class="muted small disclosure">
            本页内容由 AI 生成或辅助生成，画面为演示/创意合成，不代表真实拍摄。
          </p>
        </aside>
      </div>

      <section v-if="script" class="panel timeline">
        <div class="panel-title">
          <span>分镜时间线</span>
          <span class="pill">{{ script.shots.length }} 镜 · {{ script.duration }}s</span>
        </div>
        <div class="shot-list">
          <article v-for="shot in script.shots" :key="shot.id" class="shot-row">
            <div class="shot-thumb">
              <img
                v-if="shot.keyframe_path"
                :src="`${mediaUrl(shot.keyframe_path)}?v=${imageVersion}`"
                :alt="`镜头 ${shot.idx}`"
              />
              <span v-else>{{ String(shot.idx).padStart(2, '0') }}</span>
            </div>
            <div class="shot-body">
              <div class="shot-head">
                <strong>#{{ String(shot.idx).padStart(2, '0') }} · {{ shot.beat }}</strong>
                <span class="pill">{{ shot.seconds }}s</span>
              </div>
              <p class="vo">{{ shot.voiceover || '（无口播）' }}</p>
              <p class="scene">{{ shot.scene }}</p>
              <details>
                <summary>查看 Prompt</summary>
                <p class="prompt">{{ shot.prompt }}</p>
              </details>
            </div>
            <div class="shot-status">
              <span class="dot" :class="shot.status" />
              {{ shot.status === 'video_ready' ? '已出片' : shot.keyframe_path ? '分镜图' : '待生成' }}
              <button
                v-if="script"
                class="btn btn-small"
                type="button"
                :disabled="!!regeneratingShot"
                @click="regenerateShot(shot)"
              >
                {{ regeneratingShot === shot.id ? '生成中…' : '重生成' }}
              </button>
            </div>
          </article>
        </div>
      </section>
    </template>

    <div v-else-if="!loading" class="empty">项目不存在或已被删除。</div>
  </div>
</template>

<style scoped>
.back {
  display: inline-block;
  margin-bottom: 10px;
  font-size: 12px;
  color: var(--muted);
  text-decoration: none;
}

.back:hover {
  color: var(--ink);
}

.meta-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.pill.success {
  color: var(--green);
  background: #e8f6ee;
  border-color: #c9e9d8;
}

.pill.warning {
  color: var(--amber);
  background: #fdf3df;
  border-color: #f2dfb6;
}

.pill.danger {
  color: var(--red);
  background: #fdecea;
  border-color: #f5cfca;
}

.content {
  margin-top: 20px;
  align-items: start;
}

.player-card {
  overflow: hidden;
  background: #17110d;
  display: flex;
  flex-direction: column;
}

.player-card video {
  width: 100%;
  max-height: 68vh;
  background: #000;
  display: block;
}

.player-empty {
  position: relative;
  aspect-ratio: 9 / 16;
  max-height: 620px;
  margin: 18px auto 0;
  width: min(100%, 350px);
  border-radius: 14px;
  overflow: hidden;
  background: linear-gradient(160deg, #3a2f27, #1d1712);
  display: grid;
  place-items: center;
}

.player-empty img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.player-empty .placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  color: rgba(255, 255, 255, 0.55);
  font-size: 12px;
}

.player-empty .placeholder .el-icon {
  font-size: 38px;
}

.player-hint {
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 16px;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.55);
  color: rgba(255, 255, 255, 0.78);
  font-size: 11px;
  text-align: center;
}

.player-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  color: rgba(255, 255, 255, 0.5);
  font-size: 11px;
  letter-spacing: 0.5px;
}

.ai-badge {
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  color: rgba(255, 255, 255, 0.65);
}

.side {
  position: sticky;
  top: 24px;
}

.brief strong {
  font-size: 16px;
}

.brief p {
  margin: 6px 0 10px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.price {
  margin-top: 10px;
  color: var(--accent-deep);
  font-weight: 700;
  font-size: 13px;
}

.side-title {
  margin: 0 0 10px;
  font-size: 12px;
  letter-spacing: 1px;
  color: var(--muted);
}

.checklist {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 13px;
  color: #b0a599;
}

.checklist li::before {
  content: '○';
  margin-right: 8px;
  color: #c5b9a9;
}

.checklist li.done {
  color: var(--ink);
}

.checklist li.done::before {
  content: '●';
  color: var(--green);
}

.side-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.side-actions .btn {
  width: 100%;
}

.compliance {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.compliance-item {
  display: flex;
  gap: 9px;
  font-size: 12px;
  line-height: 1.6;
}

.compliance-item strong {
  color: var(--ink);
}

.compliance-item p {
  margin: 2px 0 0;
  color: var(--muted);
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-top: 5px;
  flex: none;
  background: #c5b9a9;
}

.dot.block,
.dot.danger {
  background: var(--red);
}

.dot.warn {
  background: var(--amber);
}

.dot.video_ready,
.dot.keyframe_ready {
  background: var(--green);
}

.disclosure {
  line-height: 1.7;
  color: #a3978a;
}

.timeline {
  margin-top: 18px;
}

.shot-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.shot-row {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr) 70px;
  gap: 14px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-2);
}

.shot-thumb {
  width: 92px;
  aspect-ratio: 9 / 16;
  border-radius: 8px;
  overflow: hidden;
  background: #e7ddcf;
  display: grid;
  place-items: center;
  color: #a99c8c;
  font-size: 18px;
  font-weight: 700;
}

.shot-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.shot-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.shot-head strong {
  font-size: 13px;
}

.vo {
  margin: 6px 0 4px;
  font-size: 13px;
  line-height: 1.6;
}

.scene {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}

details {
  margin-top: 6px;
}

summary {
  cursor: pointer;
  font-size: 11px;
  color: #9a8f83;
}

.prompt {
  margin: 8px 0 0;
  padding: 9px 10px;
  border-radius: 8px;
  background: #f2ece2;
  font-size: 11px;
  line-height: 1.7;
  color: #6f6256;
}

.shot-status {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  justify-content: flex-end;
  font-size: 11px;
  color: var(--muted);
  padding-top: 2px;
}

@media (max-width: 1100px) {
  .side {
    position: static;
  }

  .shot-row {
    grid-template-columns: 70px minmax(0, 1fr);
  }

  .shot-thumb {
    width: 70px;
  }

  .shot-status {
    grid-column: 2;
    justify-content: flex-start;
  }
}
</style>
