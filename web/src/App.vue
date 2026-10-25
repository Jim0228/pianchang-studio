<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from './stores/app'

const route = useRoute()
const store = useAppStore()

const nav = [
  { to: '/', label: '工作台', icon: 'HomeFilled' },
  { to: '/projects/new', label: '新建视频', icon: 'VideoCamera' },
  { to: '/skills', label: '风格库', icon: 'Collection' },
  { to: '/tasks', label: '任务中心', icon: 'List' },
  { to: '/settings', label: '设置', icon: 'Setting' },
]

const isActive = (item: { to: string }) =>
  item.to === '/' ? route.path === '/' : route.path.startsWith(item.to)

const modeLabel = computed(() => (store.mode === 'real' ? 'REAL · 方舟' : 'MOCK · 演示'))

onMounted(() => {
  store.loadSettings().catch(() => undefined)
})
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <router-link to="/" class="brand">
        <span class="brand-mark">▶</span>
        <span class="brand-text">
          <strong>片场</strong>
          <em>STUDIO</em>
        </span>
      </router-link>

      <nav class="nav">
        <router-link
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          class="nav-item"
          :class="{ active: isActive(item) }"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>

      <div class="sidebar-foot">
        <div class="mode-pill" :class="store.mode">{{ modeLabel }}</div>
        <p>本地运行 · 素材不离开电脑</p>
        <p class="version">v{{ store.settings?.version || '0.1.0' }}</p>
      </div>
    </aside>

    <main class="main">
      <router-view v-slot="{ Component }">
        <transition name="page" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr);
  min-height: 100vh;
}

.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  padding: 28px 18px 22px;
  display: flex;
  flex-direction: column;
  background: var(--brown);
  color: #f8efe3;
  overflow: hidden;
}

.sidebar::after {
  content: '';
  position: absolute;
  right: -110px;
  bottom: -140px;
  width: 300px;
  height: 300px;
  border-radius: 50%;
  background: rgba(255, 90, 54, 0.18);
  filter: blur(6px);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 8px 26px;
  color: inherit;
  text-decoration: none;
}

.brand-mark {
  width: 42px;
  height: 42px;
  border-radius: 14px;
  background: var(--accent);
  display: grid;
  place-items: center;
  font-size: 16px;
  color: #fff;
  box-shadow: 0 8px 20px rgba(255, 90, 54, 0.35);
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1;
}

.brand-text strong {
  font-size: 20px;
  letter-spacing: 2px;
}

.brand-text em {
  margin-top: 6px;
  font-size: 10px;
  letter-spacing: 3px;
  font-style: normal;
  color: rgba(248, 239, 227, 0.55);
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 6px;
  position: relative;
  z-index: 1;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 12px;
  color: rgba(248, 239, 227, 0.66);
  font-size: 14px;
  text-decoration: none;
  transition: 0.18s ease;
}

.nav-item:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.06);
}

.nav-item.active {
  color: #fff;
  background: rgba(255, 90, 54, 0.18);
  box-shadow: inset 3px 0 0 var(--accent);
}

.nav-item .el-icon {
  font-size: 17px;
}

.sidebar-foot {
  margin-top: auto;
  padding: 0 8px;
  position: relative;
  z-index: 1;
}

.mode-pill {
  display: inline-flex;
  align-items: center;
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 11px;
  letter-spacing: 1px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  color: #ffd9a8;
  background: rgba(255, 255, 255, 0.06);
}

.mode-pill.real {
  color: #b7f5d4;
  border-color: rgba(101, 224, 158, 0.35);
}

.sidebar-foot p {
  margin: 10px 0 0;
  font-size: 11px;
  line-height: 1.6;
  color: rgba(248, 239, 227, 0.42);
}

.sidebar-foot .version {
  margin-top: 2px;
  letter-spacing: 1px;
}

.main {
  min-width: 0;
  padding: 34px 40px 64px;
}

@media (max-width: 900px) {
  .shell {
    grid-template-columns: 1fr;
  }

  .sidebar {
    position: relative;
    height: auto;
    padding: 16px;
    flex-direction: row;
    align-items: center;
    gap: 14px;
  }

  .brand {
    padding: 0;
  }

  .brand-text em,
  .sidebar-foot p {
    display: none;
  }

  .nav {
    flex-direction: row;
    overflow-x: auto;
  }

  .nav-item {
    white-space: nowrap;
    padding: 8px 10px;
  }

  .nav-item span {
    display: none;
  }

  .sidebar-foot {
    margin: 0 0 0 auto;
  }

  .main {
    padding: 22px 18px 48px;
  }
}
</style>
