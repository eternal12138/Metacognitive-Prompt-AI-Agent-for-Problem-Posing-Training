import importlib
import os
import pathlib
import re
import tempfile
import unittest


TEST_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["APP_ENV"] = "test"
os.environ["APP_DATA_DIR"] = TEST_DATA_DIR.name
os.environ["FLASK_SECRET_KEY"] = "test-only-secret-key-with-at-least-32-characters"
os.environ.pop("ARK_API_KEY", None)
os.environ.pop("EMBEDDING_API_KEY", None)

app_module = importlib.import_module("app")


def csrf_from(response):
    match = re.search(
        rb'name="_csrf_token"\s+value="([^"]+)"',
        response.data,
    )
    if not match:
        raise AssertionError("CSRF token not found in login response")
    return match.group(1).decode("utf-8")


class ApplicationSecurityTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        app_module.task_pool.shutdown(wait=False, cancel_futures=True)
        system = getattr(app_module.chroma_client, "_system", None)
        if system is not None:
            system.stop()
        with app_module.app.app_context():
            app_module.db.session.remove()
            for engine in app_module.db.engines.values():
                engine.dispose()
        TEST_DATA_DIR.cleanup()

    def setUp(self):
        self.client = app_module.app.test_client()

    def login(self, username="admin", password="123456"):
        login_page = self.client.get("/login")
        token = csrf_from(login_page)
        response = self.client.post(
            "/login",
            data={
                "username": username,
                "password": password,
                "_csrf_token": token,
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            return session["_csrf_token"]

    def test_security_headers_and_mobile_fallback(self):
        response = self.client.get("/login", headers={"User-Agent": "Android"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_csrf_rejects_mutation_without_token(self):
        response = self.client.post(
            "/api/user/change_password",
            json={"new_password": "new-password"},
        )
        self.assertEqual(response.status_code, 400)

    def test_anonymous_user_cannot_list_users(self):
        response = self.client.get("/api/users/list")
        self.assertEqual(response.status_code, 401)

    def test_admin_can_list_users_with_csrf_session(self):
        self.login()
        response = self.client.get("/api/users/list")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.get_json()), 2)

    def test_teacher_and_student_pages_render(self):
        self.login()
        teacher_page = self.client.get("/teacher")
        self.assertEqual(teacher_page.status_code, 200)
        self.assertIn(b"X-CSRF-Token", teacher_page.data)

        student_client = app_module.app.test_client()
        login_page = student_client.get("/login")
        token = csrf_from(login_page)
        login_response = student_client.post(
            "/login",
            data={
                "username": "2026001",
                "password": "123456",
                "_csrf_token": token,
            },
        )
        self.assertEqual(login_response.status_code, 302)
        student_page = student_client.get("/student")
        self.assertEqual(student_page.status_code, 200)
        self.assertIn(b"X-CSRF-Token", student_page.data)

    def test_ai_route_requires_configuration(self):
        csrf_token = self.login("2026001", "123456")
        response = self.client.post(
            "/api/ai_evaluate",
            json={"question": "为什么会这样？"},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
