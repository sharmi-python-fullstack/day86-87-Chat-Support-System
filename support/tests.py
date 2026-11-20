import os
from unittest.mock import AsyncMock, Mock, patch

import requests
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import StaffProfile, Ticket
from .views import _gemini_generate, assign_priority

User = get_user_model()


class StaffTicketTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="customer", password="test-password"
        )
        self.staff_user = User.objects.create_user(
            username="agent", password="test-password"
        )
        self.staff_profile = StaffProfile.objects.create(
            user=self.staff_user, department=StaffProfile.Department.TECHNICAL
        )
        self.ticket = Ticket.objects.create(
            user=self.customer,
            subject="Cannot sign in",
            description="The sign-in page shows an error.",
        )

    def test_bootstrap_superuser_skips_without_credentials(self):
        credentials = {
            "DJANGO_SUPERUSER_USERNAME": "",
            "DJANGO_SUPERUSER_EMAIL": "",
            "DJANGO_SUPERUSER_PASSWORD": "",
        }
        with patch.dict(os.environ, credentials):
            call_command("bootstrap_superuser")

        self.assertFalse(User.objects.filter(is_superuser=True).exists())

    def test_bootstrap_superuser_creates_admin_idempotently(self):
        credentials = {
            "DJANGO_SUPERUSER_USERNAME": "site-admin",
            "DJANGO_SUPERUSER_EMAIL": "site-admin@example.com",
            "DJANGO_SUPERUSER_PASSWORD": "Very-Long-Unique-Secret-2026!",
        }
        with patch.dict(os.environ, credentials):
            call_command("bootstrap_superuser")
            call_command("bootstrap_superuser")

        admin = User.objects.get(username="site-admin")
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.check_password(credentials["DJANGO_SUPERUSER_PASSWORD"]))
        self.assertEqual(User.objects.filter(username="site-admin").count(), 1)

    def test_bootstrap_superuser_does_not_elevate_existing_client(self):
        credentials = {
            "DJANGO_SUPERUSER_USERNAME": self.customer.username,
            "DJANGO_SUPERUSER_EMAIL": "customer@example.com",
            "DJANGO_SUPERUSER_PASSWORD": "Very-Long-Unique-Secret-2026!",
        }
        with patch.dict(os.environ, credentials):
            with self.assertRaises(CommandError):
                call_command("bootstrap_superuser")

        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_superuser)

    def test_staff_login_accepts_staff_and_redirects_to_queue(self):
        response = self.client.post(
            reverse("staff_login"),
            {"username": "agent", "password": "test-password"},
        )

        self.assertRedirects(response, reverse("staff_dashboard"))
        self.assertEqual(
            self.client.session["_auth_user_id"], str(self.staff_user.pk)
        )

    def test_client_login_accepts_client_and_redirects_to_tickets(self):
        response = self.client.post(
            reverse("login"),
            {"username": "customer", "password": "test-password"},
        )

        self.assertRedirects(response, reverse("create_ticket"))
        self.assertEqual(self.client.session["_auth_user_id"], str(self.customer.pk))

    def test_staff_login_rejects_client_account(self):
        response = self.client.post(
            reverse("staff_login"),
            {"username": "customer", "password": "test-password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This account is not registered as staff.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_client_login_rejects_staff_account(self):
        response = self.client.post(
            reverse("login"),
            {"username": "agent", "password": "test-password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please use the staff sign-in page")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_staff_dashboard_redirects_to_staff_login(self):
        response = self.client.get(reverse("staff_dashboard"))

        self.assertRedirects(
            response,
            f"{reverse('staff_login')}?next={reverse('staff_dashboard')}",
        )

    def test_staff_can_claim_an_unassigned_ticket(self):
        self.client.force_login(self.staff_user)

        response = self.client.post(reverse("claim_ticket", args=[self.ticket.pk]))

        self.assertRedirects(response, reverse("staff_dashboard"))
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assigned_to, self.staff_profile)

    def test_staff_can_open_an_unassigned_ticket_from_the_queue(self):
        self.client.force_login(self.staff_user)

        queue = self.client.get(reverse("staff_dashboard"))
        detail = self.client.get(reverse("ticket_detail", args=[self.ticket.pk]))

        self.assertEqual(queue.status_code, 200)
        self.assertContains(queue, "Ticket queue")
        self.assertContains(queue, "My tickets")
        self.assertEqual(detail.status_code, 200)

    def test_non_staff_cannot_claim_a_ticket(self):
        self.client.force_login(self.customer)

        response = self.client.post(reverse("claim_ticket", args=[self.ticket.pk]))

        self.assertEqual(response.status_code, 403)
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.assigned_to)

    def test_ticket_owner_can_view_and_reply(self):
        self.client.force_login(self.customer)

        page = self.client.get(reverse("ticket_detail", args=[self.ticket.pk]))
        response = self.client.post(
            reverse("ticket_detail", args=[self.ticket.pk]),
            {"content": "I can provide more details."},
        )

        self.assertEqual(page.status_code, 200)
        self.assertRedirects(
            response, reverse("ticket_detail", args=[self.ticket.pk])
        )
        self.assertEqual(self.ticket.messages.get().content, "I can provide more details.")

    @override_settings(GEMINI_API_KEY="")
    def test_faq_endpoint_reports_missing_gemini_configuration(self):
        self.client.force_login(self.customer)

        response = self.client.post(
            reverse("get_faq_answer"),
            data='{"question":"How do I reset my password?"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("error", response.json())

    @patch("support.views.assign_priority", new_callable=AsyncMock)
    def test_ticket_creation_uses_ai_priority_and_department(self, classify):
        classify.return_value = {
            "priority": Ticket.Priority.HIGH,
            "department": "Technical",
        }
        self.client.force_login(self.customer)

        response = self.client.post(
            reverse("create_ticket"),
            {"subject": "Login fails", "description": "Urgent access issue."},
        )

        self.assertEqual(response.status_code, 302)
        ticket = Ticket.objects.get(subject="Login fails")
        self.assertEqual(ticket.priority, Ticket.Priority.HIGH)
        self.assertEqual(ticket.assigned_to, self.staff_profile)

    @patch("support.views.requests.post")
    @override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="gemini-3.8-flash")
    def test_gemini_generate_uses_interactions_api_without_schema(self, post):
        response = Mock()
        response.json.return_value = {
            "steps": [
                {
                    "type": "model_output",
                    "content": [{"type": "text", "text": "Use the reset link."}],
                }
            ]
        }
        post.return_value = response

        result = async_to_sync(_gemini_generate)("How do I reset my password?")

        self.assertEqual(result, "Use the reset link.")
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(
            post.call_args.args[0],
            "https://generativelanguage.googleapis.com/v1beta/interactions",
        )
        self.assertEqual(post.call_args.kwargs["timeout"], 15)
        self.assertEqual(
            post.call_args.kwargs["headers"],
            {"x-goog-api-key": "test-key"},
        )
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "model": "gemini-3.8-flash",
                "input": "How do I reset my password?",
                "store": False,
            },
        )

    @patch("support.views.requests.post")
    @override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="gemini-3.8-flash")
    def test_assign_priority_parses_gemini_classification(self, post):
        response = Mock()
        response.json.return_value = {
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "text",
                            "text": '{"priority":"Low","department":"Billing"}',
                        }
                    ],
                }
            ]
        }
        post.return_value = response

        result = async_to_sync(assign_priority)("Invoice question", "Need a copy.")

        self.assertEqual(result, {"priority": "Low", "department": "Billing"})
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(
            post.call_args.args[0],
            "https://generativelanguage.googleapis.com/v1beta/interactions",
        )
        self.assertEqual(post.call_args.kwargs["timeout"], 15)
        self.assertEqual(
            post.call_args.kwargs["headers"],
            {
                "x-goog-api-key": "test-key",
                "Api-Revision": "2026-05-20",
            },
        )
        request_data = post.call_args.kwargs["json"]
        self.assertEqual(request_data["model"], "gemini-3.8-flash")
        self.assertEqual(request_data["store"], False)
        self.assertNotIn("generation_config", request_data)
        self.assertEqual(
            request_data["response_format"],
            {
                "type": "text",
                "mime_type": "application/json",
                "schema": {
                    "type": "object",
                    "properties": {
                        "priority": {
                            "type": "string",
                            "enum": [choice[0] for choice in Ticket.Priority.choices],
                        },
                        "department": {
                            "type": "string",
                            "enum": [
                                choice[0] for choice in StaffProfile.Department.choices
                            ],
                        },
                    },
                    "required": ["priority", "department"],
                },
            },
        )

    @patch("support.views._gemini_generate", new_callable=AsyncMock)
    def test_faq_endpoint_returns_gemini_answer(self, generate):
        generate.return_value = "Use the password reset link on the sign-in page."
        self.client.force_login(self.customer)

        response = self.client.post(
            reverse("get_faq_answer"),
            data='{"question":"How do I reset my password?"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"answer": "Use the password reset link on the sign-in page."},
        )
        generate.assert_awaited_once()

    @patch("support.views._gemini_generate", new_callable=AsyncMock)
    def test_faq_endpoint_explains_invalid_api_key(self, generate):
        error_response = Mock()
        error_response.status_code = 403
        error_response.json.return_value = {
            "error": {"message": "API key not valid."}
        }
        generate.side_effect = requests.HTTPError(response=error_response)
        self.client.force_login(self.customer)

        with self.assertLogs("support.views", level="ERROR") as log:
            response = self.client.post(
                reverse("get_faq_answer"),
                data='{"question":"How do I reset my password?"}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("Check the API key", response.json()["error"])
        self.assertIn("API key not valid.", "\n".join(log.output))

    @patch("support.views._gemini_generate", new_callable=AsyncMock)
    @override_settings(GEMINI_API_KEY="test-key")
    def test_faq_endpoint_logs_redacted_non_json_error(self, generate):
        error_response = Mock()
        error_response.status_code = 400
        error_response.headers = {"Content-Type": "text/html"}
        error_response.text = "<html>Rejected test-key</html>"
        error_response.json.side_effect = ValueError("not JSON")
        generate.side_effect = requests.HTTPError(response=error_response)
        self.client.force_login(self.customer)

        with self.assertLogs("support.views", level="ERROR") as log:
            response = self.client.post(
                reverse("get_faq_answer"),
                data='{"question":"How do I reset my password?"}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 502)
        log_output = "\n".join(log.output)
        self.assertIn("text/html", log_output)
        self.assertIn("[REDACTED]", log_output)
        self.assertNotIn("test-key", log_output)
