<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  api,
  mediaUrl,
  waitGeneration,
  type Asset,
  type Generation,
  type Product,
  type Project,
  type Script,
  type Skill,
  type Video,
} from '../api'
import { useAppStore } from '../stores/app'
import SkillCard from '../components/SkillCard.vue'
import GenerationProgress from '../components/GenerationProgress.vue'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

const step = ref(0)
const projectId = ref('')
const project = ref<Project | null>(null)
const product = ref<Product | null>(null)
const assets = ref<Asset[]>([])
const files = ref<File[]>([])
const localPreviews = ref<string[]>([])
const form = reactive({ name: '', description: '', price: '' })
const briefDraft = reactive({
  title: '',
  category: '',
  selling_points: '',
  audience: '',
  price: '',
  tone: '',
})
const recommendations = ref<Skill[]>([])
const selectedSkill = ref<Skill | null>(null)
const script = ref<Script | null>(null)
const generation = ref<Generation | null>(null)
const video = ref<Video | null>(null)
const busy = ref(false)
const busyText = ref('')
const previsReady = ref(false)
const pageLoading = ref(false)

const displaySkills = computed(() => (recommendations.value.length ? recommendations.value : store.skills))
const totalSeconds = computed(() =>
  (script.value?.shots || []).reduce((sum, shot) => sum + Number(shot.seconds || 0), 0),
)
const progressTitle = computed(() => (generation.value?.stage === 'render' ? '成片生成' : '关键帧预演'))

function normalizeScript(value: Script): Script {
  if (!value.caption) value.caption = {}
  if (!value.caption.hashtags) value.caption.hashtags = []
  if (!value.shots) value.shots = []
  return value
}

function syncBrief(value: Product) {
  product.value = value
  const brief = value.brief || {}
  briefDraft.title = brief.title || value.title || ''
  briefDraft.category = brief.category || value.category || ''
  briefDraft.selling_points = (brief.selling_points || value.selling_points || []).join('，')
  briefDraft.audience = brief.audience || value.audience || ''
  briefDraft.price = brief.price || value.price || ''
  briefDraft.tone = brief.tone || value.tone || ''
}

function addFiles(list: File[]) {
  const images = list.filter((file) => file.type.startsWith('image/'))
  const remaining = 9 - assets.value.length - files.value.length
  if (remaining <= 0) {
    ElMessage.warning('最多上传 9 张产品图')
    return
  }
  files.value = [...files.value, ...images.slice(0, remaining)]
  refreshLocalPreviews()
}

function refreshLocalPreviews() {
  localPreviews.value.forEach((url) => URL.revokeObjectURL(url))
  localPreviews.value = files.value.map((file) => URL.createObjectURL(file))
}

function removeLocal(index: number) {
  URL.revokeObjectURL(localPreviews.value[index])
  files.value.splice(index, 1)
  refreshLocalPreviews()
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  addFiles(Array.from(input.files || []))
  input.value = ''
}

function onDrop(event: DragEvent) {
  addFiles(Array.from(event.dataTransfer?.files || []))
}

async function loadExisting(id: string) {
  pageLoading.value = true
  try {
    const detail = await api.project(id)
    projectId.value = detail.project.id
    project.value = detail.project
    assets.value = detail.assets
    form.name = detail.project.name
    form.description = detail.project.description
    form.price = detail.product?.price || ''
    script.value = detail.script ? normalizeScript(detail.script) : null
    video.value = detail.video
    generation.value = detail.latest_generation

    if (detail.product) {
      syncBrief(detail.product)
      try {
        recommendations.value = (await api.recommend(id)).items
      } catch {
        recommendations.value = []
      }
    }

    if (script.value) {
      const skill = store.skills.find((item) => item.id === script.value?.skill_id)
      if (skill) selectedSkill.value = skill
      previsReady.value = script.value.shots.some((shot) => !!shot.keyframe_path)
    } else if (recommendations.value.length) {
      selectedSkill.value = recommendations.value[0]
    }

    step.value = video.value ? 3 : script.value ? 2 : detail.product ? 1 : 0
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    pageLoading.value = false
  }
}

onMounted(async () => {
  try {
    if (!store.skills.length) await store.loadSkills()
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
  const id = route.query.project
  if (typeof id === 'string' && id) await loadExisting(id)
})

onUnmounted(() => {
  localPreviews.value.forEach((url) => URL.revokeObjectURL(url))
})

async function submitProduct() {
  if (!form.name.trim() && !form.description.trim()) {
    ElMessage.warning('请至少填写项目名或商品描述')
    return
  }
  busy.value = true
  busyText.value = '正在保存商品资料…'
  try {
    const description =
      form.description.trim() + (form.price.trim() ? `，价格 ${form.price.trim()} 元` : '')
    if (!projectId.value) {
      const created = await api.createProject({
        name: form.name.trim() || description.slice(0, 20),
        description,
        platform: 'douyin',
        duration: 15,
        ratio: '9:16',
      })
      projectId.value = created.id
      project.value = created
    } else {
      project.value = await api.updateProject(projectId.value, {
        name: form.name.trim() || description.slice(0, 20),
        description,
        platform: 'douyin',
        duration: 15,
        ratio: '9:16',
      })
    }

    if (files.value.length) {
      busyText.value = `正在上传 ${files.value.length} 张产品图…`
      const uploaded = await api.upload(projectId.value, files.value)
      assets.value = [...assets.value, ...uploaded]
      files.value = []
      refreshLocalPreviews()
    }

    busyText.value = 'AI 正在理解商品…'
    const analyzed = await api.analyze(projectId.value)
    syncBrief(analyzed)

    busyText.value = '正在匹配视频风格…'
    recommendations.value = (await api.recommend(projectId.value)).items
    selectedSkill.value = recommendations.value[0] || null
    step.value = 1
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    busy.value = false
  }
}

async function saveBrief() {
  if (!product.value) return
  const points = briefDraft.selling_points
    .split(/[，,、\n]+/)
    .map((item) => item.trim())
    .filter(Boolean)
  try {
    const updated = await api.updateProduct(product.value.id, {
      title: briefDraft.title,
      category: briefDraft.category,
      selling_points: points,
      audience: briefDraft.audience,
      price: briefDraft.price,
      tone: briefDraft.tone,
    })
    syncBrief(updated)
    recommendations.value = (await api.recommend(projectId.value)).items
    ElMessage.success('商品档案已保存')
  } catch (error) {
    ElMessage.error((error as Error).message)
  }
}

async function generateScript() {
  if (!selectedSkill.value) {
    ElMessage.warning('请选择一个视频风格')
    return
  }
  busy.value = true
  busyText.value = '正在写脚本与分镜…'
  try {
    script.value = normalizeScript(
      await api.createScript(projectId.value, {
        skill_id: selectedSkill.value.id,
        platform: 'douyin',
        duration: 15,
        ratio: '9:16',
      }),
    )
    step.value = 2
    previsReady.value = false
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    busy.value = false
  }
}

function scriptPayload() {
  if (!script.value) return null
  return {
    title: script.value.title,
    caption_title: script.value.caption?.title || '',
    caption_body: script.value.caption?.body || '',
    hashtags: script.value.caption?.hashtags || [],
    shots: script.value.shots.map((shot) => ({
      idx: shot.idx,
      seconds: Number(shot.seconds),
      scene: shot.scene,
      camera: shot.camera,
      action: shot.action,
      voiceover: shot.voiceover,
      on_screen: shot.on_screen,
      prompt: shot.prompt,
    })),
  }
}

async function saveScript(showToast = true) {
  const payload = scriptPayload()
  if (!payload || !script.value) return script.value
  const updated = await api.patchScript(script.value.id, payload)
  script.value = normalizeScript(updated)
  if (showToast) ElMessage.success('脚本已保存')
  return script.value
}

async function startPrevis() {
  if (!script.value) return
  busy.value = true
  try {
    busyText.value = '正在保存脚本…'
    await saveScript(false)
    busyText.value = '正在生成关键帧…'
    generation.value = await api.previs(script.value.id)
    step.value = 3
    const final = await waitGeneration(generation.value.id, (item) => {
      generation.value = item
    })
    if (final.status !== 'succeeded') throw new Error(final.error || '预演失败')
    script.value = normalizeScript(await api.script(script.value.id))
    previsReady.value = true
    ElMessage.success('预演完成，可以生成成片了')
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    busy.value = false
  }
}

async function startRender() {
  if (!script.value) return
  busy.value = true
  try {
    busyText.value = '正在保存脚本…'
    await saveScript(false)
    busyText.value = '正在生成成片…'
    generation.value = await api.render(script.value.id)
    step.value = 3
    const final = await waitGeneration(generation.value.id, (item) => {
      generation.value = item
    })
    if (final.status !== 'succeeded') throw new Error(final.error || '成片生成失败')
    const detail = await api.project(projectId.value)
    script.value = detail.script ? normalizeScript(detail.script) : script.value
    video.value = detail.video
    previsReady.value = true
    ElMessage.success('成片已生成')
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    busy.value = false
  }
}

async function exportVideo() {
  if (!video.value) return
  busy.value = true
  busyText.value = '正在打包 ZIP…'
  try {
    const result = await api.exportVideo(video.value.id)
    window.open(api.downloadUrl(result.id), '_blank')
    ElMessage.success('导出完成，开始下载')
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    busy.value = false
  }
}

function openProject() {
  if (projectId.value) router.push(`/projects/${projectId.value}`)
}

function updateHashtags(value: string) {
  if (!script.value) return
  script.value.caption.hashtags = value
    .split(/[\s,，#]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}
</script>

<template>
  <div class="page wizard" v-loading="busy || pageLoading" :element-loading-text="busyText">
    <div class="page-head">
      <div>
        <p class="eyebrow">NEW PROJECT</p>
        <h1 class="page-title">{{ projectId ? '继续创作' : '新建视频' }}</h1>
        <p class="page-sub">四步完成：商品资料 → 视频风格 → 脚本分镜 → 预演与成片。所有操作都会自动保存。</p>
      </div>
      <div class="actions">
        <router-link class="btn btn-ghost" to="/">取消</router-link>
        <button v-if="projectId" class="btn" type="button" @click="openProject">打开项目页</button>
      </div>
    </div>

    <el-steps :active="step" align-center finish-status="success" class="steps">
      <el-step title="商品资料" description="图 + 描述" />
      <el-step title="视频风格" description="Skill 推荐" />
      <el-step title="脚本分镜" description="可改可删" />
      <el-step title="预演成片" description="导出物料" />
    </el-steps>

    <section v-if="step === 0" class="panel step-panel">
      <div class="grid two-col">
        <div class="form-col">
          <label class="field-label">项目名</label>
          <el-input v-model="form.name" size="large" placeholder="例如：厨房油污清洁剂" />
          <label class="field-label">商品描述</label>
          <el-textarea
            v-model="form.description"
            :rows="6"
            placeholder="写清楚品类、规格、核心卖点、适用人群。例如：500ml 植物配方，不伤手，一喷一擦，厨房灶台油烟机都能用。"
          />
          <label class="field-label">价格（可选）</label>
          <el-input v-model="form.price" placeholder="例如：39.9" />
          <p class="hint">描述越具体，脚本和分镜越贴近真实卖点。没有图片也可以先跑通流程。</p>
        </div>

        <div class="upload-col">
          <label class="dropzone" @dragover.prevent @drop.prevent="onDrop">
            <input type="file" multiple accept="image/*" hidden @change="onFileChange" />
            <el-icon><UploadFilled /></el-icon>
            <strong>拖拽或点击上传产品图</strong>
            <span>JPG / PNG / WebP · 最多 9 张 · 单张 ≤ 15MB</span>
          </label>

          <div class="preview-grid" v-if="assets.length || localPreviews.length">
            <div v-for="asset in assets" :key="asset.id" class="thumb">
              <img :src="asset.url" :alt="asset.filename" />
            </div>
            <div v-for="(src, index) in localPreviews" :key="src" class="thumb local">
              <img :src="src" alt="待上传" />
              <button type="button" @click="removeLocal(index)">×</button>
            </div>
          </div>
          <p class="hint">建议白底或干净背景的单品图；多主体、带竞品水印的图会影响一致性。</p>
        </div>
      </div>

      <div class="step-actions">
        <span class="step-note">Mock 模式不会调用外部模型，可直接演示。</span>
        <button class="btn btn-primary" type="button" :disabled="busy" @click="submitProduct">
          {{ busy ? busyText : '保存并解析商品' }}
          <el-icon><ArrowRight /></el-icon>
        </button>
      </div>
    </section>

    <template v-else-if="step === 1">
      <div class="grid two-col">
        <section class="panel">
          <h2 class="panel-title">商品档案</h2>
          <label class="field-label">商品名</label>
          <el-input v-model="briefDraft.title" />
          <label class="field-label">品类</label>
          <el-input v-model="briefDraft.category" />
          <label class="field-label">卖点（用逗号分隔）</label>
          <el-input v-model="briefDraft.selling_points" />
          <div class="grid two-col tight">
            <div>
              <label class="field-label">人群</label>
              <el-input v-model="briefDraft.audience" />
            </div>
            <div>
              <label class="field-label">价格</label>
              <el-input v-model="briefDraft.price" />
            </div>
          </div>
          <label class="field-label">调性</label>
          <el-input v-model="briefDraft.tone" />
          <div class="inline-actions">
            <button class="btn" type="button" @click="saveBrief">保存档案</button>
          </div>
        </section>

        <section class="panel">
          <h2 class="panel-title">平台与时长</h2>
          <div class="pill-row">
            <span class="pill accent">抖音</span>
            <span class="pill">9:16</span>
            <span class="pill">15 秒</span>
            <span class="pill">1080P</span>
          </div>
          <p class="hint">
            首版固定为抖音 9:16 / 15 秒。其他平台（小红书、闲鱼、淘宝、拼多多）的预设已内置，后续版本开放变体渲染。
          </p>
          <div class="divider" />
          <h3 class="side-title">当前选择</h3>
          <div v-if="selectedSkill" class="selected-skill">
            <strong>{{ selectedSkill.name }}</strong>
            <p>{{ selectedSkill.reason || `${selectedSkill.beats} 个镜头，${selectedSkill.goal}导向` }}</p>
          </div>
          <p v-else class="muted small">还没有选择风格。</p>
        </section>
      </div>

      <div class="section-head">
        <div>
          <p class="eyebrow">RECOMMENDED</p>
          <h2>推荐风格</h2>
        </div>
        <span class="muted small">按品类、目标与平台打分</span>
      </div>
      <div class="skill-grid">
        <SkillCard
          v-for="skill in displaySkills"
          :key="skill.id"
          :skill="skill"
          :selected="selectedSkill?.id === skill.id"
          show-reason
          @select="selectedSkill = skill"
        />
      </div>

      <div class="step-actions">
        <button class="btn" type="button" @click="step = 0">上一步</button>
        <button class="btn btn-primary" type="button" :disabled="!selectedSkill || busy" @click="generateScript">
          {{ busy ? busyText : '生成脚本与分镜' }}
          <el-icon><ArrowRight /></el-icon>
        </button>
      </div>
    </template>

    <template v-else-if="step === 2 && script">
      <section class="panel">
        <div class="script-head">
          <div class="script-title">
            <label class="field-label">脚本标题</label>
            <el-input v-model="script.title" />
          </div>
          <span class="pill" :class="{ accent: totalSeconds === script.duration }">
            {{ totalSeconds }}s / {{ script.duration }}s
          </span>
        </div>

        <div class="grid two-col">
          <div>
            <label class="field-label">发布标题</label>
            <el-input v-model="script.caption.title" />
          </div>
          <div>
            <label class="field-label">话题标签（空格分隔）</label>
            <el-input
              :model-value="(script.caption.hashtags || []).join(' ')"
              @update:model-value="updateHashtags"
            />
          </div>
        </div>
        <label class="field-label">正文</label>
        <el-textarea v-model="script.caption.body" :rows="3" />
      </section>

      <div class="section-head">
        <div>
          <p class="eyebrow">STORYBOARD</p>
          <h2>分镜脚本</h2>
        </div>
        <span class="muted small">字幕建议 ≤18 字，口播 ≤40 字</span>
      </div>

      <div class="shot-editor" v-for="shot in script.shots" :key="shot.id">
        <div class="shot-editor-head">
          <div>
            <span class="shot-index">#{{ String(shot.idx).padStart(2, '0') }}</span>
            <span class="tag">{{ shot.beat }}</span>
          </div>
          <div class="seconds">
            <span class="muted small">时长</span>
            <el-input-number v-model="shot.seconds" :min="1" :max="10" :step="1" size="small" />
            <span class="muted small">秒</span>
          </div>
        </div>
        <div class="grid two-col">
          <div>
            <label class="field-label">画面</label>
            <el-textarea v-model="shot.scene" :rows="2" />
          </div>
          <div>
            <label class="field-label">运镜</label>
            <el-input v-model="shot.camera" />
          </div>
        </div>
        <label class="field-label">口播</label>
        <el-input v-model="shot.voiceover" />
        <label class="field-label">字幕</label>
        <el-input v-model="shot.on_screen" maxlength="18" show-word-limit />
        <details>
          <summary>查看 / 编辑 Prompt</summary>
          <el-textarea v-model="shot.prompt" :rows="4" class="prompt-input" />
        </details>
      </div>

      <div class="step-actions">
        <button class="btn" type="button" @click="step = 1">上一步</button>
        <button class="btn" type="button" @click="saveScript()">保存草稿</button>
        <button class="btn btn-primary" type="button" :disabled="busy" @click="startPrevis">
          {{ busy ? busyText : '生成预演' }}
          <el-icon><VideoPlay /></el-icon>
        </button>
      </div>
    </template>

    <template v-else-if="step === 3 && script">
      <div class="grid two-col">
        <section class="panel">
          <h2 class="panel-title">预演 / 成片</h2>
          <GenerationProgress :generation="generation" :title="progressTitle" />
          <div class="inline-actions">
            <button class="btn" type="button" :disabled="busy" @click="startPrevis">重新生成关键帧</button>
            <button class="btn btn-primary" type="button" :disabled="busy || !previsReady" @click="startRender">
              {{ video ? '重新生成成片' : '生成成片' }}
              <el-icon><VideoPlay /></el-icon>
            </button>
          </div>
          <p class="hint">建议先确认关键帧与字幕，再生成终版。Mock 模式下每镜约 1–3 秒。</p>
        </section>

        <section class="panel">
          <h2 class="panel-title">关键帧</h2>
          <div class="keyframe-grid">
            <div v-for="shot in script.shots" :key="shot.id" class="keyframe-card">
              <img v-if="shot.keyframe_path" :src="mediaUrl(shot.keyframe_path)" :alt="`镜头 ${shot.idx}`" />
              <div v-else class="keyframe-empty">待生成</div>
              <span>#{{ String(shot.idx).padStart(2, '0') }} · {{ shot.seconds }}s</span>
            </div>
          </div>
        </section>
      </div>

      <section v-if="video" class="panel video-result">
        <div class="panel-title">
          <span>成片</span>
          <span class="pill">{{ video.width }}×{{ video.height }} · {{ video.duration.toFixed(1) }}s</span>
        </div>
        <div class="video-wrap">
          <video controls playsinline :src="video.url" :poster="video.cover?.url" />
          <div class="video-side">
            <p class="muted small">导出包含 MP4、封面、脚本、SRT、文案与 manifest。</p>
            <button class="btn btn-primary" type="button" :disabled="busy" @click="exportVideo">
              <el-icon><Download /></el-icon>
              {{ busy ? busyText : '导出 ZIP' }}
            </button>
            <button class="btn" type="button" @click="openProject">打开项目页</button>
          </div>
        </div>
      </section>

      <div class="step-actions">
        <button class="btn" type="button" @click="step = 2">返回分镜</button>
        <button class="btn btn-primary" type="button" @click="openProject">完成，打开项目页</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.steps {
  margin: 8px 0 26px;
  padding: 18px 12px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.65);
}

.step-panel {
  margin-top: 4px;
}

.form-col,
.upload-col {
  padding: 4px;
}

.field-label {
  display: block;
  margin: 14px 0 6px;
  font-size: 12px;
  color: var(--muted);
}

.field-label:first-child {
  margin-top: 0;
}

.grid.tight {
  gap: 10px;
  margin-top: 0;
}

.hint {
  margin: 12px 0 0;
  font-size: 11px;
  line-height: 1.7;
  color: #9a8f83;
}

.dropzone {
  min-height: 230px;
  border: 1.5px dashed #d4c7b6;
  border-radius: 16px;
  background: var(--surface-2);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  cursor: pointer;
  text-align: center;
  padding: 24px;
  transition: 0.2s ease;
}

.dropzone:hover {
  border-color: var(--accent);
  background: #fff7f3;
}

.dropzone .el-icon {
  font-size: 34px;
  color: var(--accent);
}

.dropzone strong {
  font-size: 14px;
}

.dropzone span {
  font-size: 11px;
  color: var(--muted);
}

.preview-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 12px;
}

.thumb {
  position: relative;
  aspect-ratio: 1;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid var(--line);
  background: #efe7d9;
}

.thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.thumb button {
  position: absolute;
  right: 4px;
  top: 4px;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  cursor: pointer;
  line-height: 1;
}

.step-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--line);
}

.step-note {
  margin-right: auto;
  font-size: 11px;
  color: #9a8f83;
}

.inline-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 16px;
}

.pill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.selected-skill strong {
  font-size: 15px;
}

.selected-skill p {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--muted);
}

.side-title {
  margin: 0 0 10px;
  font-size: 12px;
  letter-spacing: 1px;
  color: var(--muted);
}

.skill-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.section-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin: 28px 0 14px;
}

.section-head h2 {
  margin: 0;
  font-size: 22px;
}

.script-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.script-title {
  flex: 1;
}

.shot-editor {
  margin-top: 14px;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface);
}

.shot-editor-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.shot-index {
  font-weight: 700;
  margin-right: 8px;
}

.seconds {
  display: flex;
  align-items: center;
  gap: 8px;
}

details {
  margin-top: 12px;
}

summary {
  cursor: pointer;
  font-size: 11px;
  color: #9a8f83;
}

.prompt-input {
  margin-top: 8px;
}

.keyframe-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.keyframe-card {
  position: relative;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid var(--line);
  background: var(--surface-2);
}

.keyframe-card img {
  width: 100%;
  aspect-ratio: 9 / 16;
  object-fit: cover;
  display: block;
}

.keyframe-empty {
  aspect-ratio: 9 / 16;
  display: grid;
  place-items: center;
  color: #b5a999;
  font-size: 12px;
}

.keyframe-card span {
  position: absolute;
  left: 6px;
  bottom: 6px;
  padding: 3px 6px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 10px;
}

.video-result {
  margin-top: 18px;
}

.video-wrap {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 240px;
  gap: 18px;
}

.video-wrap video {
  width: 100%;
  max-height: 66vh;
  border-radius: 12px;
  background: #000;
}

.video-side {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.video-side .btn {
  width: 100%;
}

@media (max-width: 1100px) {
  .skill-grid,
  .keyframe-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .video-wrap {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 700px) {
  .skill-grid,
  .keyframe-grid {
    grid-template-columns: 1fr;
  }

  .step-actions {
    flex-wrap: wrap;
  }

  .step-note {
    width: 100%;
  }
}
</style>
