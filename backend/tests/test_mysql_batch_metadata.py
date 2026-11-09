"""MySQL regression for metadata-only batch update retaining source and resource."""
import io
import json
import uuid
import zipfile

import pytest

from test_fixtures import login_headers

pytestmark = pytest.mark.mysql


def test_update_after_skip_keeps_draft_lesson_document_and_resource(mysql_app, mysql_data):
    import batch_worker

    title = "qa-batch-metadata-" + uuid.uuid4().hex
    md = b"# Safe test source\n\nBody\n"
    resource = b"Safe test attachment\n"
    item = {"title": title, "description": "original", "price": 0,
            "resource": "resources/file.txt", "lessons": [{"title": "Lesson", "duration": 5,
            "contentFile": "lessons/body.md", "documentFile": "lessons/body.md", "preview": True}]}
    with mysql_app.test_client() as client:
        author = login_headers(client, mysql_data["users"]["author"])

        def upload(items, files=None, mode="skip", publish=False):
            package = io.BytesIO()
            with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("toolkits.json", json.dumps(items))
                for name, body in (files or {}).items():
                    archive.writestr(name, body)
            package.seek(0)
            response = client.post("/api/batch/upload", headers=author, data={
                "package": (package, "batch.zip"), "mode": mode, "publish": "1" if publish else "0"})
            assert response.status_code == 202, response.json
            job_id = response.json["jobId"]
            with mysql_app.app_context():
                assert batch_worker.claim("qa-metadata-worker") == job_id
                batch_worker.load_and_process(mysql_app, job_id, "qa-metadata-worker")
            result = client.get(f"/api/batch/jobs/{job_id}", headers=author)
            assert result.status_code == 200, result.json
            return result.json

        files = {"lessons/body.md": md, "resources/file.txt": resource}
        created = upload([item], files)
        assert created["status"] == "done" and created["created"] == 1, created
        toolkit_id = created["results"][0]["toolkitId"]
        original = client.get(f"/api/toolkits/{toolkit_id}", headers=author).json
        skipped = upload([item], files, publish=True)
        assert skipped["status"] == "done" and skipped["skipped"] == 1, skipped
        updated = upload([{"title": title, "description": "updated", "price": 0}],
                         mode="update", publish=True)
        assert updated["status"] == "done" and updated["updated"] == 1, updated
        after = client.get(f"/api/toolkits/{toolkit_id}", headers=author).json
        assert after["status"] == "draft" and after["description"] == "updated", after
        assert after["lessons"][0]["id"] == original["lessons"][0]["id"], (original, after)
        assert after["resources"][0]["refId"] == original["resources"][0]["refId"], (original, after)
        lesson_id = after["lessons"][0]["id"]
        ref_id = after["resources"][0]["refId"]
        assert client.get(f"/api/lesson-raw/{lesson_id}", headers=author).data == md
        assert client.get(f"/api/toolkits/{toolkit_id}/resources/{ref_id}/download",
                          headers=author).data == resource
