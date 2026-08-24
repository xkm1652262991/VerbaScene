from app.models import Chapter, Project
from app.agents.style import resolve_visual_style


def plan_project(project: Project, chapter: Chapter) -> dict:
    _ = chapter
    target_duration = project.target_duration_sec or 90
    recommended_shots = min(24, max(8, round(target_duration / 8)))
    style = resolve_visual_style(project.style, project.creative_settings)

    return {
        "summary": (
            f"{project.title} 是一个动画英语短剧项目"
            f"{f'，风格为：{style}' if style else '，使用项目创作设置中的动画风格'}。"
        ),
        "style_keywords": [style] if style else [],
        "recommended_duration_sec": target_duration,
        "recommended_shot_count": recommended_shots,
        "core_conflict": "依据原文提炼主角目标、阻碍和转折。",
    }
