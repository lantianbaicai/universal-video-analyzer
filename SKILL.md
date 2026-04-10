---
name: doubao-video-analyzer
description: 使用多模态大模型分析视频内容，支持画面识别和语音转文字，生成结构化中文报告。支持豆包、智谱、通义千问等多种模型，用户自行配置 API Key。
mode: direct
---

## 触发条件
当用户发送视频文件（.mp4, .mov等）并希望分析内容时，自动触发此技能。

## 执行命令
```bash
python doubao_video_analyzer.py "{{video_path}}"
```

## 配置说明

### 必需配置
设置环境变量 `VIDEO_ANALYZER_API_KEY` 为你的多模态模型 API Key。

### 可选配置
| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `VIDEO_ANALYZER_MODEL` | 使用的模型名称 | `doubao-seed-2-0-pro-260215` |
| `VIDEO_ANALYZER_BASE_URL` | API 基础地址 | `https://ark.cn-beijing.volces.com/api/v3` |
| `WHISPER_MODEL_DIR` | Whisper 模型本地路径 | 自动下载 |

### 支持的模型示例
| 模型提供商 | MODEL 值 | BASE_URL |
|-----------|---------|----------|
| 豆包 | `doubao-seed-2-0-pro-260215` | `https://ark.cn-beijing.volces.com/api/v3` |
| 智谱 GLM-4V | `glm-4v-plus` | `https://open.bigmodel.cn/api/paas/v4` |
| 通义千问 VL | `qwen-vl-plus` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |

## 功能特点
✅ **双轨分析**：同时分析视频画面 + 语音转文字，生成完整报告
✅ **模型无关**：支持多种多模态模型，用户自由选择
✅ **结构化输出**：自动生成场景、核心信息、亮点等结构化内容
✅ **国内可用**：支持豆包、智谱、通义等国内模型，无需翻墙
✅ **容错完善**：ffmpeg错误检查、API超时保护、跨平台路径兼容
