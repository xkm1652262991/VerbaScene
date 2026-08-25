import ast
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = API_ROOT / "app"
SERVICES_ROOT = APP_ROOT / "services"
COMPATIBILITY_FACADES = {
    "app.services.asset_service": SERVICES_ROOT / "asset_service.py",
    "app.services.script_service": SERVICES_ROOT / "script_service.py",
    "app.services.video_generation_service": SERVICES_ROOT / "video_generation_service.py",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _service_imports(path: Path) -> set[str]:
    return {
        node.module
        for node in _tree(path).body
        if isinstance(node, ast.ImportFrom)
        and isinstance(node.module, str)
        and node.module.startswith("app.services.")
    }


class AssetServiceArchitectureTests(unittest.TestCase):
    def test_compatibility_facades_contain_no_business_definitions(self):
        for module, path in COMPATIBILITY_FACADES.items():
            with self.subTest(module=module):
                definitions = [
                    node
                    for node in _tree(path).body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]
                self.assertEqual(definitions, [])

    def test_internal_application_code_does_not_depend_on_compatibility_facades(self):
        offenders: list[str] = []
        for path in APP_ROOT.rglob("*.py"):
            if path in COMPATIBILITY_FACADES.values():
                continue
            imported_facades = sorted(
                module
                for module in COMPATIBILITY_FACADES
                if module in _service_imports(path)
            )
            if imported_facades:
                offenders.append(
                    f"{path.relative_to(API_ROOT)} -> {', '.join(imported_facades)}"
                )
        self.assertEqual(offenders, [])

    def test_application_modules_do_not_import_private_cross_module_helpers(self):
        offenders: list[str] = []
        for path in APP_ROOT.rglob("*.py"):
            for node in ast.walk(_tree(path)):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if not isinstance(node.module, str) or not node.module.startswith("app."):
                    continue
                private_names = [alias.name for alias in node.names if alias.name.startswith("_")]
                if private_names:
                    offenders.append(
                        f"{path.relative_to(API_ROOT)}: {node.module} -> {', '.join(private_names)}"
                    )
        self.assertEqual(offenders, [])

    def test_image_and_video_modules_keep_one_way_dependencies(self):
        image_imports = _service_imports(SERVICES_ROOT / "image_generation_service.py")
        video_imports = _service_imports(SERVICES_ROOT / "video_generation_service.py")

        self.assertTrue(
            image_imports.isdisjoint(
                {
                    "app.services.asset_service",
                    "app.services.asset_candidate_service",
                    "app.services.video_generation_service",
                }
            )
        )
        self.assertTrue(
            video_imports.isdisjoint(
                {
                    "app.services.asset_service",
                    "app.services.asset_candidate_service",
                    "app.services.image_generation_service",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
