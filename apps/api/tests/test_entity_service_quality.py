import unittest
from types import SimpleNamespace

from app.services.entity_service import (
    _serialize_entity_context,
    _with_approved_entity_context,
)


class EntityServiceContextTests(unittest.TestCase):
    def test_generation_context_contains_approved_entities(self):
        character = SimpleNamespace(
            id="character-1",
            name="Tom",
            role_type="主角",
            age="小学生",
            gender="男",
            identity="照料小鸟的孩子",
            personality="温和",
            appearance="短黑发，蓝色T恤，卡其色短裤。",
            fixed_prompt="Tom，短黑发，蓝色T恤，卡其色短裤。",
        )
        approved = _serialize_entity_context([character], [], [])

        prompt = _with_approved_entity_context(
            "基础提示词",
            approved_entities=approved,
        )

        self.assertIn("Tom", prompt)
        self.assertIn("上一版已确认资产", prompt)


if __name__ == "__main__":
    unittest.main()
