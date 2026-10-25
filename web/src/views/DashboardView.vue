<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, formatDate, mediaUrl, type Project } from '../api'
import { useAppStore } from '../stores/app'
import { projectStatus } from '../ui'

const store = useAppStore()
const projects = ref<Project[]>([])
const loading = ref(true)

onMounted(async () => {
  try {
    const [list] = await Promise.all([api.projects(), store.loadAll()])
    projects.value = list
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    loading.value = false
  }
})

const stats = [
  { key: 'projects', label: '项目', suffix: '个' },
  { key: 'videos', label: '成片', suffix: '条' },
  { key: 'running', label: '进行中', suffix: '个' },
  { key: 'skills', label: '视频风格', suffix: '种' },
]

const steps = [
  { no: '01', title: '上传商品', desc: '产品图 + 一段描述，AI 自动提炼卖点。' },
  { no: '02', title: '选择风格', desc: '按品类与目标推荐 Skill，先看结构再生成。' },
  { no: '03', title: '确认分镜', desc: '改口播、改字幕、换镜头，先出低成本预演。' },
  { no: '04', title: '导出成片', desc: '字幕、封面、文案、manifest 一次打包。' },
]
</script>

<template>
  <div class="page">
    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">AI COMMERCE VIDEO STUDIO</p>
        <h1>把商品描述，<br />变成能直接发的短视频。</h1>
        <p class="hero-sub">
          上传产品图，选一个视频风格。脚本、分镜、关键帧、成片和平台文案，在同一个工作台里完成。
        </p>
        <div class="actions">
          <router-link class="btn btn-primary" to="/projects/new">
            <el-icon><VideoCamera /></el-icon>
            开始创作
          </router-link>
          <router-link class="btn" to="/skills">
            <el-icon><Collection /></el-icon>
            浏览 6 种风格
          </router-link>
        </div>
        <div class="hero-meta">
          <span>纯本地运行</span>
          <span>Mock 可演示</span>
          <span>方舟 Seedance 可选</span>
        </div>
      </div>

      <div class="hero-art" aria-hidden="true">
        <div class="phone">
          <div class="phone-bar"><span>SHOT 01 / 05</span><span>9:16</span></div>
          <div class="phone-scene">
            <div class="phone-product">
              <span />
            </div>
            <div class="phone-caption">厨房油污，一喷一擦</div>
          </div>
          <div class="phone-foot">AI 生成 · 15s</div>
        </div>
        <div class="float-card">
          <strong>15s</strong>
          <span>5 镜 · 1080P</span>
        </div>
        <div class="float-card second">
          <strong>4 风格</strong>
          <span>可推荐 / 可换</span>
        </div>
      </div>
    </section>

    <section class="stat-row">
      <div v-for="item in stats" :key="item.key" class="stat-card">
        <span>{{ item.label }}</span>
        <strong>{{ (store.stats as any)[item.key] || 0 }}<em>{{ item.suffix }}</em></strong>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <div>
          <p class="eyebrow">RECENT WORK</p>
          <h2>最近项目</h2>
        </div>
        <router-link class="btn btn-ghost" to="/tasks">任务中心 →</router-link>
      </div>

      <div v-if="loading" class="empty">正在读取项目…</div>
      <div v-else-if="!projects.length" class="empty">
        <p>还没有项目。上传第一张产品图，开始做第一条视频。</p>
        <router-link class="btn btn-primary" to="/projects/new">新建视频</router-link>
      </div>
      <div v-else class="project-grid">
        <router-link
          v-for="project in projects.slice(0, 6)"
          :key="project.id"
          :to="`/projects/${project.id}`"
          class="project-card"
        >
          <div class="project-cover" :class="{ filled: !!project.cover }">
            <img v-if="project.cover" :src="mediaUrl(project.cover.path)" :alt="project.name" />
            <div v-else class="cover-fallback">
              <span>{{ project.name.slice(0, 1) }}</span>
            </div>
            <span class="project-status" :class="projectStatus(project.status).tone">
              {{ projectStatus(project.status).label }}
            </span>
          </div>
          <div class="project-body">
            <strong>{{ project.name }}</strong>
            <p>{{ project.description || '暂无描述' }}</p>
            <div class="project-meta">
              <span>{{ project.duration }}s · {{ project.ratio }}</span>
              <span>{{ formatDate(project.updated_at || project.created_at) }}</span>
            </div>
          </div>
        </router-link>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <div>
          <p class="eyebrow">HOW IT WORKS</p>
          <h2>一条视频的四步</h2>
        </div>
      </div>
      <div class="step-grid">
        <div v-for="step in steps" :key="step.no" class="step-card">
          <span class="step-no">{{ step.no }}</span>
          <strong>{{ step.title }}</strong>
          <p>{{ step.desc }}</p>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(320px, 0.85fr);
  gap: 36px;
  align-items: center;
  padding: 30px 6px 26px;
}

.hero h1 {
  margin: 0;
  font-size: clamp(34px, 4.2vw, 58px);
  line-height: 1.08;
  letter-spacing: -1.5px;
}

.hero-sub {
  max-width: 560px;
  margin: 18px 0 26px;
  color: var(--muted);
  font-size: 15px;
  line-height: 1.8;
}

.hero-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 22px;
}

.hero-meta span {
  font-size: 11px;
  color: var(--muted);
  padding: 5px 10px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.6);
}

.hero-art {
  position: relative;
  min-height: 430px;
  display: grid;
  place-items: center;
}

.phone {
  width: 236px;
  aspect-ratio: 9 / 16;
  border-radius: 30px;
  background: #1d1712;
  padding: 9px;
  box-shadow: 0 30px 60px rgba(43, 33, 27, 0.28);
  transform: rotate(3deg);
  position: relative;
  z-index: 1;
}

.phone-bar,
.phone-foot {
  display: flex;
  justify-content: space-between;
  padding: 7px 10px;
  font-size: 8px;
  letter-spacing: 1px;
  color: rgba(255, 255, 255, 0.66);
}

.phone-scene {
  height: calc(100% - 48px);
  border-radius: 20px;
  background: linear-gradient(160deg, #f6f1e7, #d9bd97 64%, #8a6a48);
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.phone-scene::after {
  content: '';
  position: absolute;
  right: -50px;
  top: -40px;
  width: 170px;
  height: 170px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.34);
}

.phone-product {
  width: 104px;
  height: 176px;
  border-radius: 18px;
  background: #2e2a26;
  display: grid;
  place-items: center;
  box-shadow: 0 18px 32px rgba(0, 0, 0, 0.22);
  position: relative;
  z-index: 1;
}

.phone-product span {
  width: 46px;
  height: 46px;
  border-radius: 50%;
  background: var(--accent);
}

.phone-caption {
  position: absolute;
  bottom: 26px;
  left: 16px;
  right: 16px;
  z-index: 2;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(20, 16, 12, 0.72);
  color: #ffe08a;
  font-size: 12px;
  text-align: center;
  font-weight: 700;
}

.float-card {
  position: absolute;
  left: 6%;
  bottom: 22%;
  z-index: 2;
  padding: 12px 16px;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 16px 34px rgba(43, 33, 27, 0.16);
  display: flex;
  flex-direction: column;
  gap: 4px;
  transform: rotate(-4deg);
}

.float-card strong {
  font-size: 20px;
  color: var(--accent-deep);
}

.float-card span {
  font-size: 10px;
  color: var(--muted);
}

.float-card.second {
  left: auto;
  right: 0;
  bottom: 10%;
  transform: rotate(5deg);
}

.float-card.second strong {
  color: var(--brown);
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 8px 0 38px;
}

.stat-card {
  padding: 16px 18px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.72);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stat-card span {
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 1px;
}

.stat-card strong {
  font-size: 28px;
  line-height: 1;
}

.stat-card em {
  font-size: 11px;
  font-style: normal;
  color: var(--muted);
  margin-left: 4px;
}

.section {
  margin-top: 38px;
}

.section-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.section-head h2 {
  margin: 0;
  font-size: 22px;
}

.project-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.project-card {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 16px;
  overflow: hidden;
  text-decoration: none;
  color: inherit;
  transition: 0.2s ease;
}

.project-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 18px 36px rgba(43, 33, 27, 0.1);
}

.project-cover {
  position: relative;
  height: 180px;
  background: #efe7d9;
  overflow: hidden;
}

.project-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover-fallback {
  height: 100%;
  display: grid;
  place-items: center;
  background:
    radial-gradient(320px 180px at 80% 10%, rgba(255, 90, 54, 0.18), transparent 70%),
    linear-gradient(150deg, #f6f1e7, #d8c4a7);
}

.cover-fallback span {
  font-size: 54px;
  font-weight: 800;
  color: rgba(43, 33, 27, 0.2);
}

.project-status {
  position: absolute;
  left: 12px;
  top: 12px;
  padding: 4px 9px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.9);
  color: var(--muted);
}

.project-status.success {
  color: var(--green);
}

.project-status.warning {
  color: var(--amber);
}

.project-status.danger {
  color: var(--red);
}

.project-status.info {
  color: #2f5d8a;
}

.project-body {
  padding: 14px 16px 16px;
}

.project-body strong {
  font-size: 15px;
}

.project-body p {
  margin: 7px 0 12px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 38px;
}

.project-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 11px;
  color: #9a8f83;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}

.step-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.step-card {
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.6);
}

.step-no {
  font-size: 11px;
  letter-spacing: 2px;
  color: var(--accent-deep);
  font-weight: 700;
}

.step-card strong {
  display: block;
  margin: 10px 0 6px;
  font-size: 15px;
}

.step-card p {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--muted);
}

@media (max-width: 1100px) {
  .hero {
    grid-template-columns: 1fr;
  }

  .hero-art {
    min-height: 360px;
  }

  .project-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .step-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 700px) {
  .stat-row,
  .project-grid,
  .step-grid {
    grid-template-columns: 1fr;
  }
}
</style>
