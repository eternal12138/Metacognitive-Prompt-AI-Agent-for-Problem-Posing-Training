import importlib
import os
import pathlib
import re
import tempfile
import unittest
from unittest.mock import patch


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
        app_module.task_pool.shutdown(wait=True, cancel_futures=True)
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

    def test_four_state_transitions_are_deterministic(self):
        state = app_module.MetacognitiveState(
            student_id="state-test",
            task_name="state-task",
            phase="planning",
            turn_count=0,
            prompt_count=0,
            weak_input_streak=0,
            cycle_index=1,
            started_at="2026-01-01 00:00:00",
            updated_at="2026-01-01 00:00:00",
        )
        planning = app_module.decide_metacognitive_prompt(
            state, "我想研究液体压强和深度的关系"
        )
        monitoring = app_module.decide_metacognitive_prompt(
            state, "如果深度增加，压强会如何变化？"
        )
        regulating = app_module.decide_metacognitive_prompt(
            state, "如果深度增加，压强会如何变化？"
        )
        reflecting = app_module.decide_metacognitive_prompt(
            state, "我想回顾自己的提问", action="reflect"
        )
        next_cycle = app_module.decide_metacognitive_prompt(
            state, "我准备开始新一轮探究"
        )

        self.assertEqual(planning["prompt_type"], "planning")
        self.assertEqual(monitoring["prompt_type"], "monitoring")
        self.assertEqual(regulating["prompt_type"], "regulating")
        self.assertEqual(regulating["trigger_reason"], "repeated_question")
        self.assertEqual(reflecting["prompt_type"], "reflecting")
        self.assertEqual(next_cycle["prompt_type"], "planning")
        self.assertEqual(next_cycle["cycle_index"], 2)

    def test_similar_repeated_question_triggers_regulating_prompt(self):
        state = app_module.MetacognitiveState(
            student_id="similarity-test",
            task_name="state-task",
            phase="monitoring",
            turn_count=1,
            prompt_count=1,
            weak_input_streak=0,
            last_question_normalized=app_module._normalize_question(
                "如果深度增加，液体压强会如何变化？"
            ),
            cycle_index=1,
            started_at="2026-01-01 00:00:00",
            updated_at="2026-01-01 00:00:00",
        )

        decision = app_module.decide_metacognitive_prompt(
            state,
            "如果深度增加，液体压强会如何发生变化？",
        )

        self.assertEqual(decision["prompt_type"], "regulating")
        self.assertEqual(decision["trigger_reason"], "repeated_question")

    def test_legacy_chat_history_bootstraps_monitoring_state(self):
        with app_module.app.app_context():
            app_module.db.session.add(
                app_module.ChatRecord(
                    time="2026-01-01 08:00:00",
                    student_name="迁移测试学生",
                    student_id="legacy-state-test",
                    question="材料中的液体深度发生了什么变化？",
                    ai_response="请继续观察变量之间的关系。",
                    task_name="历史任务",
                    task_content="历史材料",
                )
            )
            app_module.db.session.commit()

            state = app_module.get_or_create_metacognitive_state(
                "legacy-state-test",
                "历史任务",
            )

            self.assertEqual(state.phase, "monitoring")
            self.assertEqual(state.turn_count, 1)
            self.assertEqual(
                state.last_question_normalized,
                app_module._normalize_question("材料中的液体深度发生了什么变化？"),
            )

    def test_chat_persists_prompt_event(self):
        class FakeResponse:
            def __init__(self, router=False):
                self.router = router

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {"message": {"content": '{"intent":"socratic"}'}}
                    ]
                }

            def iter_lines(self):
                yield (
                    'data: {"choices":[{"delta":{"content":"先想一想你最想探究哪个变量？"}}]}'
                ).encode("utf-8")
                yield b"data: [DONE]"

        csrf_token = self.login("2026001", "123456")
        original_api_key = app_module.API_KEY
        app_module.API_KEY = "test-key"
        try:
            with patch.object(
                app_module.requests,
                "post",
                side_effect=[FakeResponse(router=True), FakeResponse()],
            ):
                response = self.client.post(
                    "/chat",
                    json={"message": "我想研究这个实验", "action": "chat"},
                    headers={"X-CSRF-Token": csrf_token},
                    buffered=True,
                )
        finally:
            app_module.API_KEY = original_api_key

        self.assertEqual(response.status_code, 200)
        with app_module.app.app_context():
            event = app_module.PromptEvent.query.filter_by(
                student_id="2026001"
            ).order_by(app_module.PromptEvent.id.desc()).first()
            self.assertIsNotNone(event)
            self.assertEqual(event.prompt_type, "planning")
            self.assertEqual(event.trigger_reason, "task_cycle_started")
            self.assertIn("最想探究", event.prompt_text)

    def test_reflection_skips_router_and_vector_retrieval(self):
        class FakeStreamResponse:
            def raise_for_status(self):
                return None

            def iter_lines(self):
                yield (
                    'data: {"choices":[{"delta":{"content":"回顾一下，你的问题后来增加了哪些条件？"}}]}'
                ).encode("utf-8")
                yield b"data: [DONE]"

        csrf_token = self.login("2026001", "123456")
        with app_module.app.app_context():
            app_module.PromptEvent.query.filter_by(student_id="2026001").delete()
            app_module.MetacognitiveState.query.filter_by(student_id="2026001").delete()
            app_module.ChatRecord.query.filter_by(student_id="2026001").delete()
            app_module.db.session.commit()

        original_api_key = app_module.API_KEY
        app_module.API_KEY = "test-key"
        try:
            with patch.object(
                app_module.requests,
                "post",
                return_value=FakeStreamResponse(),
            ) as post_mock, patch.object(
                app_module,
                "get_text_embedding",
            ) as embedding_mock:
                response = self.client.post(
                    "/chat",
                    json={"message": "", "action": "reflect"},
                    headers={"X-CSRF-Token": csrf_token},
                    buffered=True,
                )
        finally:
            app_module.API_KEY = original_api_key

        self.assertEqual(response.status_code, 200)
        self.assertEqual(post_mock.call_count, 1)
        embedding_mock.assert_not_called()
        with app_module.app.app_context():
            event = app_module.PromptEvent.query.filter_by(
                student_id="2026001"
            ).order_by(app_module.PromptEvent.id.desc()).first()
            self.assertIsNotNone(event)
            self.assertEqual(event.prompt_type, "reflecting")
            self.assertEqual(event.trigger_reason, "student_requested_reflection")


if __name__ == "__main__":
    unittest.main()
