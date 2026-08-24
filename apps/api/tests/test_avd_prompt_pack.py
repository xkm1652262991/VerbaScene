import unittest

from app.agents.avd_prompt_pack import BUILTIN_AVD_PROMPT_TEMPLATES


class AVDPromptPackTests(unittest.TestCase):
    def test_builtin_prompt_pack_covers_core_agent_types(self):
        agent_types = {template.agent_type for template in BUILTIN_AVD_PROMPT_TEMPLATES}

        self.assertTrue(
            {
                "script_rewriter",
                "script_reviewer",
                "extractor",
                "storyboard_breaker",
                "shot_video",
            }
            <= agent_types
        )
        self.assertNotIn("sound_designer", agent_types)

    def test_runtime_prompt_pack_is_data_driven(self):
        contents = "\n".join(template.content for template in BUILTIN_AVD_PROMPT_TEMPLATES)

        self.assertIn("Skill 只提供生成结构、连续性约束和质检标准", contents)
        self.assertIn("不提供默认题材、时代、流派或情绪", contents)
        self.assertIn("只从当前剧本提取角色、场景和跨镜头关键道具", contents)
        self.assertIn("一次调用完成镜头结构", contents)
        self.assertIn("visible_character_ids", contents)
        self.assertIn("英文对白逐字写入", contents)


if __name__ == "__main__":
    unittest.main()
