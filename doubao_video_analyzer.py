"""
多模态视频分析器 - doubao-video-analyzer
支持画面识别 + 语音转文字，生成结构化中文报告
支持豆包、智谱、通义千问等多模型，用户自行配置 API Key
"""

import sys
import os
import base64
import json
import shutil
import tempfile
import subprocess
import logging
from pathlib import Path
from datetime import datetime

# 可选依赖
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import requests
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    from PIL import Image
except ImportError as e:
    print(f"❌ 缺少依赖: {e}")
    print("   请运行: pip install requests tenacity Pillow")
    sys.exit(1)

import whisper

# ========== 日志配置 ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ========== 输入参数校验 ==========
if len(sys.argv) < 2:
    print("使用方法：python doubao_video_analyzer.py <视频文件路径>")
    print("示例：python doubao_video_analyzer.py C:\\videos\\demo.mp4")
    print("      python doubao_video_analyzer.py demo.mp4 -o report.md")
    sys.exit(1)

video_arg = sys.argv[1]
output_arg = None
if len(sys.argv) >= 4 and sys.argv[2] == "-o":
    output_arg = sys.argv[3]
elif len(sys.argv) >= 4 and sys.argv[2] == "--output":
    output_arg = sys.argv[3]

VIDEO_PATH = Path(video_arg).resolve()
if not VIDEO_PATH.exists():
    logger.error(f"视频文件不存在: {VIDEO_PATH}")
    sys.exit(1)
if not VIDEO_PATH.suffix.lower() in [".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"]:
    logger.warning(f"非标准视频格式: {VIDEO_PATH.suffix}，尝试继续处理")

# ========== 从环境变量读取配置（支持多模型） ==========
API_KEY = os.environ.get("VIDEO_ANALYZER_API_KEY", "")
MODEL = os.environ.get("VIDEO_ANALYZER_MODEL", "doubao-seed-2-0-pro-260215")
BASE_URL = os.environ.get("VIDEO_ANALYZER_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "base")
FRAME_COUNT = int(os.environ.get("FRAME_COUNT", "5"))
MAX_FRAME_WIDTH = int(os.environ.get("MAX_FRAME_WIDTH", "800"))
CUSTOM_PROMPT = os.environ.get("ANALYSIS_PROMPT", "")

if not API_KEY:
    logger.error("请设置环境变量 VIDEO_ANALYZER_API_KEY")
    logger.info("   Windows: $env:VIDEO_ANALYZER_API_KEY=\"你的密钥\"")
    logger.info("   Linux/macOS: export VIDEO_ANALYZER_API_KEY=\"你的密钥\"")
    logger.info("   或创建 .env 文件，写入 VIDEO_ANALYZER_API_KEY=你的密钥")
    sys.exit(1)

OUTPUT_DIR = VIDEO_PATH.parent
BASE_NAME = VIDEO_PATH.stem

# ========== 视频时长校验 ==========
def get_video_duration(video_path: Path) -> float:
    """获取视频时长（秒）"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip()) if result.stdout else 0
    except Exception:
        return 0

duration = get_video_duration(VIDEO_PATH)
if duration > 0 and duration < 3:
    logger.warning("视频时长小于3秒，跳过分析")
    sys.exit(0)
logger.info(f"📹 正在处理视频: {VIDEO_PATH.name} (时长: {duration:.1f}秒)")

# ========== 核心处理逻辑（使用临时目录自动清理） ==========
try:
    # 创建临时目录（程序结束自动删除）
    with tempfile.TemporaryDirectory(prefix=f"{BASE_NAME}_") as temp_dir:
        FRAMES_DIR = Path(temp_dir)
        AUDIO_PATH = FRAMES_DIR / f"{BASE_NAME}_audio.wav"

        # 1. 抽关键帧（I帧）
        logger.info("⏳ 正在提取关键帧...")
        try:
            subprocess.run(
                ["ffmpeg", "-i", str(VIDEO_PATH),
                 "-vf", "select='eq(pict_type,I)'",
                 "-vsync", "vfr",
                 str(FRAMES_DIR / "frame_%04d.jpg")],
                capture_output=True, check=True, timeout=120
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"ffmpeg 抽帧失败: {e}")
            logger.info("   请确认已安装 ffmpeg: https://ffmpeg.org/download.html")
            sys.exit(1)
        except subprocess.TimeoutExpired:
            logger.error("ffmpeg 抽帧超时（120秒），视频可能过大或损坏")
            sys.exit(1)
        except FileNotFoundError:
            logger.error("ffmpeg 未找到，请先安装 ffmpeg")
            sys.exit(1)

        # 2. 提取音频
        logger.info("⏳ 正在提取音频...")
        try:
            subprocess.run(
                ["ffmpeg", "-i", str(VIDEO_PATH),
                 "-ac", "1", "-ar", "16000",
                 str(AUDIO_PATH)],
                capture_output=True, check=True, timeout=120
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"ffmpeg 音频提取失败: {e}")
            sys.exit(1)
        except subprocess.TimeoutExpired:
            logger.error("ffmpeg 音频提取超时（120秒）")
            sys.exit(1)

        # 3. 收集并压缩关键帧
        frame_files = sorted([f for f in FRAMES_DIR.glob("*.jpg")])[:FRAME_COUNT]
        if not frame_files:
            logger.error("未提取到有效帧，请检查视频格式")
            sys.exit(1)

        logger.info(f"⏳ 正在压缩 {len(frame_files)} 个关键帧...")
        for f in frame_files:
            try:
                with Image.open(f) as img:
                    if img.width > MAX_FRAME_WIDTH:
                        ratio = MAX_FRAME_WIDTH / img.width
                        new_height = int(img.height * ratio)
                        img = img.resize((MAX_FRAME_WIDTH, new_height), Image.Resampling.LANCZOS)
                        img.save(f, quality=80)
            except Exception as e:
                logger.warning(f"压缩帧失败 {f.name}: {e}，使用原始帧")

        logger.info(f"✅ 已提取并压缩 {len(frame_files)} 个关键帧（最大宽度 {MAX_FRAME_WIDTH}px）")

        # 4. 语音转文字
        logger.info("⏳ 正在识别语音...")
        model_dir = os.environ.get("WHISPER_MODEL_DIR", "")
        if model_dir and os.path.isdir(model_dir):
            model_path = os.path.join(model_dir, f"{WHISPER_MODEL_SIZE}.pt")
        else:
            model_path = WHISPER_MODEL_SIZE

        try:
            if os.path.exists(model_path):
                model = whisper.load_model(model_path)
            else:
                model = whisper.load_model(WHISPER_MODEL_SIZE)
        except Exception as e:
            logger.error(f"Whisper 模型加载失败: {e}")
            sys.exit(1)

        try:
            result = model.transcribe(str(AUDIO_PATH), language=None)
            transcript = result["text"]
            logger.info(f"✅ 语音识别完成（{len(transcript)} 字）")
        except Exception as e:
            logger.warning(f"语音识别失败: {e}，将仅分析画面")
            transcript = "（无音频或识别失败）"

        # 5. 构建多模态 API 请求
        logger.info(f"⏳ 正在调用 {MODEL} 分析...")

        default_prompt = f"""结合以下视频关键帧画面和音频文字稿，给出一个完整的视频内容总结。要求包含：
- 场景、人物、动作
- 核心信息、关键对话
- 视频的整体目的和亮点

音频文字稿：
{transcript}
"""
        prompt_text = CUSTOM_PROMPT if CUSTOM_PROMPT else default_prompt

        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_text}
            ]
        }]
        for f in frame_files:
            with open(f, "rb") as img:
                b64 = base64.b64encode(img.read()).decode()
                messages[0]["content"].append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                })

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": MODEL,
            "messages": messages,
            "max_tokens": 2000
        }

        # API 请求（带重试）
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type((requests.exceptions.ConnectionError, requests.exceptions.Timeout))
        )
        def call_api():
            resp = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            return resp.json()

        try:
            resp_json = call_api()
            final_output = resp_json["choices"][0]["message"]["content"]
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if hasattr(e, 'response') else 0
            if status_code == 401:
                logger.error("❌ API密钥无效，请检查 VIDEO_ANALYZER_API_KEY")
            elif status_code == 429:
                logger.error("❌ API请求限流，请稍后重试或检查模型配额")
            elif status_code == 404:
                logger.error(f"❌ 模型 {MODEL} 不存在，请检查 VIDEO_ANALYZER_MODEL")
            else:
                logger.error(f"❌ API请求失败（状态码 {status_code}）: {e}")
            sys.exit(1)
        except requests.exceptions.Timeout:
            logger.error("❌ API 请求超时（60秒），请检查网络或模型响应速度")
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ API 请求失败: {e}")
            sys.exit(1)
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.error(f"❌ API 返回解析失败: {e}")
            sys.exit(1)

        # 6. 输出结果
        print(f"\n{'='*50}")
        print("📊 分析结果：")
        print(f"{'='*50}")
        print(final_output)

        # 7. 保存分析报告
        if output_arg:
            report_path = Path(output_arg).resolve()
        else:
            report_path = OUTPUT_DIR / f"{BASE_NAME}_分析报告.md"

        report_content = f"""# 视频分析报告

**来源**: {VIDEO_PATH.name}
**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**模型**: {MODEL}
**时长**: {duration:.1f}秒

---

{final_output}
"""
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report_content)
            logger.info(f"✅ 分析报告已保存至: {report_path}")
        except Exception as e:
            logger.warning(f"保存报告失败: {e}")

except Exception as e:
    logger.error(f"❌ 处理过程中发生错误: {e}")
    sys.exit(1)

logger.info("✅ 全部完成！")
