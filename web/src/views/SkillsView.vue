<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { api, type Skill } from '../api'
import { useAppStore } from '../stores/app'
import SkillCard from '../components/SkillCard.vue'

const store = useAppStore()
const router = useRouter()
const skills = ref<Skill[]>([])
const loading = ref(true)
const dialog = ref(false)
const detail = ref<Skill | null>(null)
const detailLoading = ref(false)

onMounted(async () => {
  try {
    if (!store.skills.length) await store.loadSkills()
    skills.value = store.skills
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    loading.value = false
  }
})

async function open(skill: Skill) {
  dialog.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await api.skill(skill.id)
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    detailLoading.value = false
  }
}

const bannerStyle = computed(() => {
  const palette = detail.value?.palette?.length ? detail.value.palette : ['#F6F1E7', '#E8D9C5']
  return { background: `linear-gradient(135deg, ${palette[0]}, ${palette[1]})` }
})

function useSkill() {
  dialog.value = false
  router.push('/projects/new')
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <p class="eyebrow">STYLE LIBRARY</p>
        <h1 class="page-title">视频风格库</h1>
        <p class="page-sub">
          每个 Skill 不只是 Prompt：它定义了叙事节拍、镜头数、运镜、字幕、音频与质检项。选风格，就是选一套可复用的拍摄方法。
        </p>
      </div>
      <router-link class="btn btn-primary" to="/projects/new">用风格开始创作</router-link>
    </div>

    <div v-if="loading" class="empty">正在载入风格…</div>
    <div v-else class="skill-grid">
      <SkillCard v-for="skill in skills" :key="skill.id" :skill="skill" @select="open" />
    </div>

    <el-dialog v-model="dialog" width="720px" :show-close="true" class="skill-dialog">
      <div v-if="detailLoading" class="empty">读取风格详情…</div>
      <div v-else-if="detail" class="detail">
        <div class="detail-banner" :style="bannerStyle">
          <span class="pill accent">{{ detail.goal }}</span>
          <h2>{{ detail.name }}</h2>
          <p>{{ detail.beats }} 个镜头 · {{ detail.suitable_categories.join(' / ') }}</p>
        </div>
        <div class="detail-body">
          <div class="detail-block">
            <h3>适用平台</h3>
            <div class="chips">
              <span v-for="p in detail.platforms" :key="p" class="tag">{{ p }}</span>
            </div>
          </div>
          <div class="detail-block">
            <h3>叙事节拍</h3>
            <ol class="beat-list">
              <li v-for="(beat, index) in detail.config?.beats || []" :key="index">
                <span class="beat-index">{{ index + 1 }}</span>
                <strong>{{ beat.id }}</strong>
                <span>{{ beat.purpose }}</span>
                <em>{{ beat.seconds }}s</em>
              </li>
            </ol>
          </div>
          <div class="detail-block">
            <h3>场景示例</h3>
            <ul class="scene-list">
              <li v-for="scene in detail.config?.scenes || []" :key="scene">{{ scene }}</li>
            </ul>
          </div>
          <div class="detail-actions">
            <button class="btn btn-primary" type="button" @click="useSkill">用这个风格创作</button>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.skill-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.detail-banner {
  border-radius: 14px;
  padding: 26px;
  color: #2e2a26;
  position: relative;
  overflow: hidden;
}

.detail-banner::after {
  content: '';
  position: absolute;
  right: -60px;
  top: -80px;
  width: 220px;
  height: 220px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.35);
}

.detail-banner h2 {
  margin: 14px 0 6px;
  font-size: 30px;
}

.detail-banner p {
  margin: 0;
  font-size: 12px;
  opacity: 0.7;
}

.detail-body {
  padding: 18px 4px 4px;
}

.detail-block {
  margin-bottom: 20px;
}

.detail-block h3 {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--muted);
  letter-spacing: 1px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.beat-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.beat-list li {
  display: grid;
  grid-template-columns: 26px 90px 1fr auto;
  gap: 10px;
  align-items: center;
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-2);
  font-size: 12px;
}

.beat-index {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: var(--accent-soft);
  color: var(--accent-deep);
  font-weight: 700;
}

.beat-list strong {
  font-size: 12px;
}

.beat-list span {
  color: var(--muted);
}

.beat-list em {
  font-style: normal;
  color: var(--muted);
}

.scene-list {
  margin: 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.9;
}

.detail-actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 8px;
}

@media (max-width: 1100px) {
  .skill-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 700px) {
  .skill-grid {
    grid-template-columns: 1fr;
  }
}
</style>
