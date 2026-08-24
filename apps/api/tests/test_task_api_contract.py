import json
import unittest

from app.main import app


class TaskApiContractTests(unittest.TestCase):
    def test_async_generation_endpoints_return_tasks_with_202(self):
        schema = app.openapi()
        for path in (
            "/api/projects/{project_id}/script/generate",
            "/api/shots/{shot_id}/video/generate-candidate",
            "/api/projects/{project_id}/shot-videos/generate-candidates",
            "/api/assets/{asset_id}/regenerate-video-candidate",
        ):
            operation = schema["paths"][path]["post"]
            self.assertIn("202", operation["responses"], path)
            response_schema = operation["responses"]["202"]["content"]["application/json"]["schema"]
            self.assertIn("GenerationTaskRead", json.dumps(response_schema), path)

    def test_cancel_and_retry_replace_legacy_queue_delete(self):
        paths = app.openapi()["paths"]
        self.assertIn("post", paths["/api/tasks/{task_id}/cancel"])
        self.assertIn("post", paths["/api/tasks/{task_id}/retry"])
        self.assertNotIn("/api/tasks/{task_id}/queue", paths)

    def test_task_read_exposes_recovery_metadata_but_not_lease_owner(self):
        task_schema = app.openapi()["components"]["schemas"]["GenerationTaskRead"]
        properties = task_schema["properties"]
        for field in (
            "parent_task_id",
            "retry_of_task_id",
            "resource_key",
            "heartbeat_at",
            "cancel_requested_at",
            "max_retries",
            "child_summary",
        ):
            self.assertIn(field, properties)
        self.assertNotIn("lease_owner", properties)
        self.assertNotIn("lease_expires_at", properties)

    def test_task_list_exposes_new_filters(self):
        operation = app.openapi()["paths"]["/api/tasks"]["get"]
        names = {parameter["name"] for parameter in operation["parameters"]}
        self.assertTrue(
            {"parent_task_id", "task_type", "resource_key"}.issubset(names)
        )


if __name__ == "__main__":
    unittest.main()
