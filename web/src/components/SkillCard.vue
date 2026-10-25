<script setup lang="ts">
import type { Skill } from '../api'

const props = defineProps<{
  skill: Skill
  selected?: boolean
  showReason?: boolean
}>()

defineEmits<{ select: [Skill] }>()

function gradient(skill: Skill) {
  const palette = skill.palette && skill.palette.length >= 2 ? skill.palette : ['#F6F1E7', '#E8D9C5', '#2E2A26']
  return `linear-gradient(145deg, ${palette[0]}, ${palette[1]})`
}
</script>

<template>
  <button
    type="button"
    class="skill-card"
    :class="{ selected, compact: showReason }"
    @click="$emit('select', props.skill)"
  >
    <div class="skill-cover" :style="{ background: gradient(skill) }">
      <span class="skill-goal">{{ skill.goal }}</span>
      <span class="skill-play">▶</span>
      <span class="skill-beats">{{ skill.beats }} 镜</span>
      <span class="skill-shine" />
    </div>
    <div class="skill-body">
      <div class="skill-head">
        <strong>{{ skill.name }}</strong>
        <span class="pill">{{ skill.category }}</span>
      </div>
      <p v-if="showReason && skill.reason" class="skill-reason">{{ skill.reason }}</p>
      <div class="skill-tags">
        <span v-for="category in skill.suitable_categories.slice(0, 3)" :key="category" class="tag">
          {{ category }}
        </span>
        <span v-if="skill.score !== undefined" class="score">{{ Math.round(skill.score * 100) }}%</span>
      </div>
    </div>
  </button>
</template>

<style scoped>
.skill-card {
  display: flex;
  flex-direction: column;
  width: 100%;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--surface);
  overflow: hidden;
  text-align: left;
  cursor: pointer;
  transition: 0.2s ease;
  font-family: inherit;
  color: var(--ink);
}

.skill-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 18px 36px rgba(43, 33, 27, 0.1);
  border-color: #d9cdbb;
}

.skill-card.selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft), 0 18px 36px rgba(255, 90, 54, 0.12);
}

.skill-cover {
  position: relative;
  height: 132px;
  padding: 12px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  overflow: hidden;
}

.skill-cover::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, transparent 30%, rgba(0, 0, 0, 0.22));
}

.skill-shine {
  position: absolute;
  width: 140px;
  height: 140px;
  right: -40px;
  top: -50px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.32);
  filter: blur(2px);
}

.skill-goal,
.skill-beats {
  position: relative;
  z-index: 1;
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.75);
  color: #3a312a;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.skill-play {
  position: absolute;
  left: 50%;
  top: 50%;
  z-index: 2;
  transform: translate(-50%, -50%);
  width: 42px;
  height: 42px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: rgba(255, 255, 255, 0.9);
  color: var(--accent);
  font-size: 13px;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.16);
}

.skill-body {
  padding: 14px 15px 15px;
}

.skill-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.skill-head strong {
  font-size: 15px;
}

.skill-reason {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--muted);
}

.skill-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
  align-items: center;
}

.score {
  margin-left: auto;
  font-size: 11px;
  font-weight: 700;
  color: var(--accent-deep);
  background: var(--accent-soft);
  padding: 3px 7px;
  border-radius: 6px;
}
</style>
