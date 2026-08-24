"""chinese director memory

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-06-16 15:00:00.000000
"""

from collections.abc import Sequence
import json

from alembic import op
from sqlalchemy import text


revision: str = "e0f1a2b3c4d5"
down_revision: str | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        text("SELECT id, title, style, target_duration_sec, resolution, aspect_ratio, director_memory FROM projects")
    ).mappings()
    for row in rows:
        memory = row["director_memory"] or {}
        if isinstance(memory, dict) and "说明" in memory and "视觉规则" in memory:
            continue

        target_duration = (
            row["target_duration_sec"]
            or (memory.get("narrative_style") or {}).get("target_duration_sec")
            or (memory.get("叙事规则") or {}).get("目标时长秒")
            or 90
        )
        style = (row["style"] or "").strip()
        chinese_memory = {
            "说明": "导演记忆是项目级创作约束，会被剧本、资产、分镜、图片提示词和视频提示词读取，用来保持同一集的风格、节奏和连续性。",
            "风格设定": {
                "用户指定风格": style,
                "风格标签": [style] if style else [],
                "未指定风格处理": "不主动添加新的题材、美术流派或情绪标签，只遵循原文已有风格。",
            },
            "视觉规则": {
                "色彩与美术": style or "跟随原文，不使用默认题材色彩方案",
                "光影": "光源方向明确，服务剧情和人物情绪，不额外添加未声明风格",
                "画面形态": "AI 漫剧，横屏 16:9；具体美术形态跟随项目风格和原文",
            },
            "叙事规则": {
                "节奏": "短视频节奏，开头明确钩子，冲突推进紧凑",
                "情绪曲线": "铺垫 -> 升级 -> 转折 -> 落点",
                "对白": "短、准、有冲突，便于 TTS 和字幕",
                "目标时长秒": target_duration,
            },
            "连续性规则": {
                "角色": ["同一角色的脸型、发型、服装轮廓和标志性道具在多镜头中保持一致。"],
                "场景": ["关键地点的空间布局、光线、气氛和原文细节在多镜头中保持一致。"],
                "道具": ["关键道具重复出现时保持形状、颜色、材质和剧情功能一致。"],
            },
            "镜头语言": {
                "默认画幅": row["aspect_ratio"] or "16:9",
                "默认分辨率": row["resolution"] or "1280x720",
                "常用景别": ["特写", "中景", "大全景/建立镜头"],
                "常用运镜": ["缓慢推进", "稳定压迫", "短距离横移"],
            },
            "提示词约束": {
                "正向约束": ["构图服务剧情", "主体清晰", "角色身份稳定", "不使用旁白"],
                "负向约束": ["水印", "乱码文字", "无关新增角色", "片头标题", "片尾字幕", "旁白"],
            },
            "生产备注": [
                f"{row['title']} 是横屏 AI 漫剧项目。",
                f"项目风格：{style or '未指定，跟随原文'}。",
            ],
            "版本": 2,
        }
        connection.execute(
            text("UPDATE projects SET director_memory = CAST(:memory AS jsonb) WHERE id = :id"),
            {"id": row["id"], "memory": json.dumps(chinese_memory, ensure_ascii=False)},
        )


def downgrade() -> None:
    return None
