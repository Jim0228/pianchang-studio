<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, type SettingsInfo } from '../api'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const settings = ref<SettingsInfo | null>(null)
const mode = ref('mock')
const provider = ref('mock')
const imageProvider = ref('mock')
const llmProvider = ref('mock')
const saving = ref(false)
const loading = ref(true)
const mcpTesting = ref(false)
const mcpMessage = ref('')
const restTesting = ref(false)
const restMessage = ref('')

async function load() {
  try {
    settings.value = await api.settings()
    mode.value = settings.value.ai_mode
    provider.value = settings.value.video_provider || 'mock'
    imageProvider.value = settings.value.image_provider || 'mock'
    llmProvider.value = settings.value.llm_provider || 'mock'
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function save() {
  saving.value = true
  try {
    await api.updateSettings({
      ai_mode: mode.value,
      video_provider: provider.value,
      image_provider: imageProvider.value,
      llm_provider: llmProvider.value,
    })
    await store.loadSettings()
    settings.value = await api.settings()
    ElMessage.success('运行设置已更新')
  } catch (error) {
    ElMessage.error((error as Error).message)
  } finally {
    saving.value = false
  }
}

async function testMcp() {
  mcpTesting.value = true
  mcpMessage.value = ''
  try {
    const result = await api.mcpStatus()
    if (result.ok) {
      mcpMessage.value = `连接成功，发现 ${result.tools?.length || 0} 个工具`
      ElMessage.success(mcpMessage.value)
    } else {
      mcpMessage.value = result.error || '连接失败'
      ElMessage.error(mcpMessage.value)
    }
  } catch (error) {
    mcpMessage.value = (error as Error).message
    ElMessage.error(mcpMessage.value)
  } finally {
    mcpTesting.value = false
  }
}

async function testRest() {
  restTesting.value = true
  restMessage.value = ''
  try {
    const result = await api.restStatus()
    if (result.ok) {
      restMessage.value = `连接成功，历史视频任务 ${result.total || 0} 条`
      ElMessage.success(restMessage.value)
    } else {
      restMessage.value = result.error || '连接失败'
      ElMessage.error(restMessage.value)
    }
  } catch (error) {
    restMessage.value = (error as Error).message
    ElMessage.error(restMessage.value)
  } finally {
    restTesting.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <p class="eyebrow">SETTINGS</p>
        <h1 class="page-title">设置</h1>
        <p class="page-sub">模型与密钥只从 .env 读取，不写入数据库、不返回前端、不记录到日志。</p>
      </div>
      <button class="btn btn-primary" type="button" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : '保存设置' }}
      </button>
    </div>

    <div v-if="loading" class="empty">读取设置…</div>
    <div v-else-if="settings" class="grid two-col">
      <div class="panel">
        <h2 class="panel-title">运行模式</h2>
        <el-radio-group v-model="mode" size="large">
          <el-radio-button value="mock">Mock 演示</el-radio-button>
          <el-radio-button value="real">真实提供方</el-radio-button>
        </el-radio-group>

        <div class="mode-note">
          <p v-if="mode === 'mock'">
            <strong>Mock 模式</strong>：全链路本地生成，不调用外部模型。适合演示、验收和离线使用。
          </p>
          <p v-else>
            <strong>Real 模式</strong>：实际能力由下方的 LLM / 图片 / 视频提供方决定；请分别配置 DeepSeek Key 或 REST Token。
          </p>
        </div>

        <div class="divider" />

        <h3 class="sub-title">LLM 提供方</h3>
        <el-radio-group v-model="llmProvider" size="large">
          <el-radio-button value="mock">Mock</el-radio-button>
          <el-radio-button value="deepseek">DeepSeek</el-radio-button>
          <el-radio-button value="ark">Ark</el-radio-button>
        </el-radio-group>
        <p class="mode-note provider-note">
          <template v-if="llmProvider === 'deepseek'">DeepSeek 负责商品解析与脚本/分镜生成，使用 DEEPSEEK_API_KEY。</template>
          <template v-else-if="llmProvider === 'ark'">Ark 负责商品解析与脚本，需要 ARK_API_KEY。</template>
          <template v-else>本地规则 + 模板生成，不调用外部 LLM。</template>
        </p>
        <el-alert
          v-if="llmProvider === 'deepseek' && !settings.deepseek_configured"
          type="warning"
          :closable="false"
          show-icon
          title="尚未检测到 DEEPSEEK_API_KEY"
          description="请在 .env 配置 DEEPSEEK_API_KEY 后重启服务。"
        />

        <div class="divider" />

        <h3 class="sub-title">图片生成提供方</h3>
        <el-radio-group v-model="imageProvider" size="large">
          <el-radio-button value="mock">Mock 本地</el-radio-button>
          <el-radio-button value="rest">REST 美术资源</el-radio-button>
          <el-radio-button value="ark">Ark</el-radio-button>
        </el-radio-group>
        <p class="mode-note provider-note">
          <template v-if="imageProvider === 'rest'">
            REST 图片生成用于成片场景板：按镜头生成干净的商品场景，再交给视频模型做图生视频。
          </template>
          <template v-else-if="imageProvider === 'ark'">Ark Seedream 生成关键帧；需要 ARK_API_KEY。</template>
          <template v-else>Pillow 本地合成场景板，不调用外部图片模型。</template>
        </p>
        <el-alert
          v-if="imageProvider === 'rest' && !settings.rest_art_configured"
          type="warning"
          :closable="false"
          show-icon
          title="REST 图片尚未配置"
          description="请在 .env 设置 REST_ART_BASE_URL 与 REST_ART_TOKEN。"
        />

        <div class="divider" />

        <h3 class="sub-title">视频生成提供方</h3>
        <el-radio-group v-model="provider" size="large">
          <el-radio-button value="mock">Mock 本地</el-radio-button>
          <el-radio-button value="rest">REST 美术资源</el-radio-button>
          <el-radio-button value="mcp">MCP 视频</el-radio-button>
          <el-radio-button value="seedance">Seedance</el-radio-button>
        </el-radio-group>
        <p class="mode-note provider-note">
          <template v-if="provider === 'rest'">
            REST 图生视频：场景板 + 原图 → 4 秒片段 → 归一化 1080×1920 → 字幕/AI 标识。
          </template>
          <template v-else-if="provider === 'mcp'">MCP 图生视频（保留为回退）。</template>
          <template v-else-if="provider === 'seedance'">火山方舟 Seedance；需要 ARK_API_KEY。</template>
          <template v-else>本地关键帧推拉 + 字幕，零外部调用。</template>
        </p>
        <el-alert
          v-if="provider === 'rest' && !settings.rest_art_configured"
          type="warning"
          :closable="false"
          show-icon
          title="REST 视频尚未配置"
          description="请在 .env 设置 REST_ART_BASE_URL 与 REST_ART_TOKEN。"
        />
        <el-alert
          v-if="provider === 'mcp' && !settings.mcp_configured"
          type="warning"
          :closable="false"
          show-icon
          title="MCP 视频尚未配置"
          description="请在 .env 设置 MCP_VIDEO_URL 与 MCP_VIDEO_TOKEN。"
        />

        <div class="inline-actions">
          <button class="btn" type="button" :disabled="restTesting || !settings.rest_art_configured" @click="testRest">
            <el-icon><Connection /></el-icon>
            {{ restTesting ? '测试中…' : '测试 REST' }}
          </button>
          <button class="btn" type="button" :disabled="mcpTesting || !settings.mcp_configured" @click="testMcp">
            {{ mcpTesting ? '测试中…' : '测试 MCP' }}
          </button>
        </div>
        <p v-if="restMessage" class="mcp-state">REST：{{ restMessage }}</p>
        <p v-if="mcpMessage" class="mcp-state">MCP：{{ mcpMessage }}</p>

        <div class="divider" />

        <h3 class="sub-title">方舟模型（备用）</h3>
        <div class="kv">
          <span>文本</span><code>{{ settings.models.chat }}</code>
          <span>视觉</span><code>{{ settings.models.vision }}</code>
          <span>图片</span><code>{{ settings.models.image }}</code>
          <span>视频</span><code>{{ settings.models.video }}</code>
        </div>
        <p class="hint">模型 ID 以火山方舟控制台实际可用为准，可在 .env 覆盖。</p>
      </div>

      <div class="panel">
        <h2 class="panel-title">运行环境</h2>
        <div class="status-list">
          <div class="status-item">
            <span>API Key</span>
            <strong :class="settings.ark_key_present ? 'ok' : 'warn'">
              {{ settings.ark_key_present ? '已配置' : '未配置' }}
            </strong>
          </div>
          <div class="status-item">
            <span>LLM 提供方</span>
            <strong>{{ settings.llm_provider }}</strong>
          </div>
          <div class="status-item">
            <span>图片提供方</span>
            <strong>{{ settings.image_provider }}</strong>
          </div>
          <div class="status-item">
            <span>视频提供方</span>
            <strong>{{ settings.video_provider }}</strong>
          </div>
          <div class="status-item">
            <span>REST 配置</span>
            <strong :class="settings.rest_art_configured ? 'ok' : 'warn'">
              {{ settings.rest_art_configured ? '已配置' : '未配置' }}
            </strong>
          </div>
          <div class="status-item">
            <span>DeepSeek</span>
            <strong :class="settings.deepseek_configured ? 'ok' : 'warn'">
              {{ settings.deepseek_configured ? '已配置' : '未配置' }}
            </strong>
          </div>
          <div class="status-item">
            <span>MCP 配置</span>
            <strong :class="settings.mcp_configured ? 'ok' : 'warn'">
              {{ settings.mcp_configured ? `已配置 · ${settings.mcp_video_resolution}` : '未配置' }}
            </strong>
          </div>
          <div class="status-item">
            <span>FFmpeg</span>
            <strong :class="settings.ffmpeg.available ? 'ok' : 'bad'">
              {{ settings.ffmpeg.available ? `可用 · ${settings.ffmpeg.codec}` : '不可用' }}
            </strong>
          </div>
          <div class="status-item">
            <span>libx264</span>
            <strong :class="settings.ffmpeg.libx264 ? 'ok' : 'warn'">
              {{ settings.ffmpeg.libx264 ? '支持' : '降级 mpeg4' }}
            </strong>
          </div>
          <div class="status-item">
            <span>风格 Skill</span>
            <strong>{{ settings.skills }} 个</strong>
          </div>
          <div class="status-item">
            <span>输出</span>
            <strong>{{ settings.video.width }}×{{ settings.video.height }} · {{ settings.video.fps }}fps</strong>
          </div>
          <div class="status-item">
            <span>同步任务</span>
            <strong>{{ settings.sync_jobs ? '开启（测试）' : '关闭' }}</strong>
          </div>
        </div>

        <div class="divider" />

        <h3 class="sub-title">数据目录</h3>
        <code class="path">{{ settings.data_dir }}</code>
        <p class="hint">数据库与素材都在这里。备份这个目录即可迁移全部项目。</p>

        <div class="divider" />

        <h3 class="sub-title">切换真实模型的步骤</h3>
        <ol class="steps">
          <li>复制 <code>.env.example</code> 为 <code>.env</code>。</li>
          <li>填写 <code>ARK_API_KEY</code> 与可用模型 ID。</li>
          <li>重启 uvicorn，再切到 Real 模式。</li>
          <li>先用一个商品跑通预演，再生成终版。</li>
        </ol>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mode-note {
  margin-top: 16px;
  padding: 14px 16px;
  border-radius: 12px;
  background: var(--surface-2);
  border: 1px solid var(--line);
  font-size: 13px;
  line-height: 1.8;
  color: var(--muted);
}

.mode-note strong {
  color: var(--ink);
}

code {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 11px;
  background: #f2ece2;
  padding: 2px 6px;
  border-radius: 5px;
  color: #5b4a3c;
}

.sub-title {
  margin: 0 0 10px;
  font-size: 12px;
  letter-spacing: 1px;
  color: var(--muted);
}

.kv {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 8px 12px;
  font-size: 12px;
  align-items: center;
}

.kv span {
  color: var(--muted);
}

.kv code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hint {
  margin: 10px 0 0;
  font-size: 11px;
  line-height: 1.7;
  color: #9a8f83;
}

.inline-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 14px;
}

.mcp-state {
  font-size: 11px;
  color: var(--muted);
}

.provider-note {
  margin-top: 14px;
}

.status-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.status-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px dashed var(--line);
  font-size: 13px;
}

.status-item:last-child {
  border-bottom: 0;
}

.status-item span {
  color: var(--muted);
}

.status-item strong {
  font-size: 12px;
}

.status-item .ok {
  color: var(--green);
}

.status-item .warn {
  color: var(--amber);
}

.status-item .bad {
  color: var(--red);
}

.path {
  display: block;
  padding: 10px 12px;
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: 8px;
  word-break: break-all;
}

.steps {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 2;
  color: var(--muted);
}
</style>
