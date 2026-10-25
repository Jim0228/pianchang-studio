import { defineStore } from 'pinia'
import { api, type SettingsInfo, type Skill } from '../api'

export const useAppStore = defineStore('app', {
  state: () => ({
    skills: [] as Skill[],
    settings: null as SettingsInfo | null,
    stats: { projects: 0, videos: 0, running: 0, skills: 0 },
    loading: false,
  }),
  getters: {
    mode: (state) => state.settings?.ai_mode || 'mock',
  },
  actions: {
    async loadSkills() {
      this.skills = await api.skills()
    },
    async loadSettings() {
      this.settings = await api.settings()
    },
    async loadStats() {
      this.stats = await api.stats()
    },
    async loadAll() {
      this.loading = true
      try {
        const [skills, settings, stats] = await Promise.all([api.skills(), api.settings(), api.stats()])
        this.skills = skills
        this.settings = settings
        this.stats = stats
      } finally {
        this.loading = false
      }
    },
    async refreshMode() {
      const settings = await api.settings()
      this.settings = settings
    },
  },
})
