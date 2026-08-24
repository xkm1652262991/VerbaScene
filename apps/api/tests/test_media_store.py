from pathlib import Path
import tempfile
import unittest

from app.platform.media import LocalMediaStore


class LocalMediaStoreTests(unittest.TestCase):
    def test_put_resolve_and_delete_keep_project_uri_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalMediaStore(temp_dir, "/storage")
            uri = store.put_bytes(
                "project-1",
                b"video",
                namespace="assets/video-candidates",
                filename="candidate.mp4",
            )

            self.assertEqual(
                uri,
                "/storage/projects/project-1/assets/video-candidates/candidate.mp4",
            )
            path = store.resolve_local_path(uri)
            self.assertEqual(path.read_bytes(), b"video")

            store.delete(uri)
            self.assertFalse(path.exists())

    def test_put_file_copies_provider_temp_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "provider-result.mp4"
            source.write_bytes(b"provider")
            store = LocalMediaStore(root / "storage", "/storage")

            uri = store.put_file(
                "project-1",
                source,
                namespace="assets/video-candidates",
            )

            self.assertEqual(store.resolve_local_path(uri).read_bytes(), b"provider")
            self.assertTrue(source.exists())

    def test_resolve_rejects_paths_outside_storage_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = LocalMediaStore(Path(temp_dir) / "storage", "/storage")
            with self.assertRaises(ValueError):
                store.resolve_local_path("/storage/../outside.mp4")


if __name__ == "__main__":
    unittest.main()
