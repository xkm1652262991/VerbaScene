import unittest

from app.agents.avd_rule_pack import merge_negative_prompt, scan_avd_prompt


class AVDRulePackTests(unittest.TestCase):
    def test_flags_readable_text_leak(self):
        issues = scan_avd_prompt("屏幕显示「提交成功」，字幕显示她的台词。", prompt_type="image")

        self.assertIn("avd_readable_text_leak", {issue.code for issue in issues})

    def test_allows_negative_text_constraint(self):
        issues = scan_avd_prompt("画面干净，no text overlay, no readable letters, no watermark.", prompt_type="image")

        self.assertNotIn("avd_readable_text_leak", {issue.code for issue in issues})

    def test_flags_impractical_camera_for_narrow_space(self):
        issues = scan_avd_prompt("便利店内，摄影机环绕慢旋并穿越货架。", prompt_type="video")

        self.assertIn("avd_impractical_camera_in_narrow_space", {issue.code for issue in issues})

    def test_merges_base_negative_prompt(self):
        prompt = merge_negative_prompt("低清晰度，水印")

        self.assertIn("可读文字", prompt)
        self.assertEqual(prompt.count("水印"), 1)

    def test_flags_temporal_sequence_in_static_image_prompt(self):
        issues = scan_avd_prompt(
            "动作瞬间：起始站立；关键动作：抬手；收束：转身离开",
            prompt_type="image",
        )

        self.assertIn("avd_static_image_temporal_sequence", {issue.code for issue in issues})

    def test_flags_camera_movement_in_static_image_prompt(self):
        issues = scan_avd_prompt("镜头：中景，缓慢前推", prompt_type="image")

        self.assertIn("avd_static_image_camera_movement", {issue.code for issue in issues})

    def test_flags_generic_animation_style(self):
        issues = scan_avd_prompt("风格DNA：动画风格\n主体：小鸟", prompt_type="image")

        self.assertIn("avd_generic_image_style", {issue.code for issue in issues})

    def test_does_not_treat_prompt_length_as_a_quality_issue(self):
        issues = scan_avd_prompt("主体：" + "窗台" * 100, prompt_type="image")

        self.assertNotIn("avd_image_prompt_too_long", {issue.code for issue in issues})


if __name__ == "__main__":
    unittest.main()
