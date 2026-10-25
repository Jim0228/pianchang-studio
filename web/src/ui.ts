export function projectStatus(status: string): { label: string; tone: string } {
  const map: Record<string, { label: string; tone: string }> = {
    draft: { label: '草稿', tone: 'neutral' },
    uploaded: { label: '已上传', tone: 'neutral' },
    analyzed: { label: '已解析', tone: 'info' },
    script_ready: { label: '脚本就绪', tone: 'info' },
    previs_running: { label: '分镜图生成中', tone: 'warning' },
    previs_ready: { label: '分镜图就绪', tone: 'warning' },
    rendering: { label: '成片生成中', tone: 'warning' },
    ready: { label: '已成片', tone: 'success' },
    error: { label: '异常', tone: 'danger' },
  }
  return map[status] || { label: status || '未知', tone: 'neutral' }
}

export function generationStatus(status: string): { label: string; tone: string } {
  const map: Record<string, { label: string; tone: string }> = {
    queued: { label: '排队中', tone: 'neutral' },
    running: { label: '生成中', tone: 'warning' },
    succeeded: { label: '已完成', tone: 'success' },
    failed: { label: '失败', tone: 'danger' },
  }
  return map[status] || { label: status || '未知', tone: 'neutral' }
}

export function stageLabel(stage: string): string {
  return (
    {
      previs: '分镜图生成',
      render: '成片渲染',
      script: '脚本生成',
      brief: '商品解析',
      export: '导出',
    }[stage] || stage
  )
}

export function platformLabel(platform: string): string {
  return (
    {
      douyin: '抖音',
      xiaohongshu: '小红书',
      xianyu: '闲鱼',
      taobao: '淘宝',
      pinduoduo: '拼多多',
      shipinhao: '视频号',
      bilibili: 'B 站',
    }[platform] || platform
  )
}
