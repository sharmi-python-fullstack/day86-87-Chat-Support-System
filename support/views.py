import asyncio
import json
import logging
from functools import partial
from urllib.parse import parse_qs

import requests
from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import MessageForm, TicketForm
from .models import StaffProfile, Ticket

logger = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


class GeminiConfigurationError(ValueError):
    pass


async def _gemini_generate(prompt, response_schema=None):
    if not settings.GEMINI_API_KEY:
        raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")

    request_data = {
        "model": settings.GEMINI_MODEL,
        "input": prompt,
        "store": False,
    }
    headers = {"x-goog-api-key": settings.GEMINI_API_KEY}
    if response_schema:
        request_data["response_format"] = {
            "type": "text",
            "mime_type": "application/json",
            "schema": response_schema,
        }
        headers["Api-Revision"] = "2026-05-20"

    response = await asyncio.to_thread(
        partial(
            requests.post,
            GEMINI_API_URL,
            headers=headers,
            json=request_data,
            timeout=15,
        )
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Gemini returned an invalid interaction response.")
    steps = payload.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Gemini returned an invalid interaction response.")

    text_parts = []
    collecting = False
    for step in reversed(steps):
        if not isinstance(step, dict):
            continue
        step_type = step.get("type")
        if step_type == "user_input":
            break
        if step_type == "thought":
            continue
        if step_type != "model_output":
            if collecting:
                break
            continue

        content = step.get("content")
        if not isinstance(content, list):
            continue
        for item in reversed(content):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
                    collecting = True
            elif collecting:
                break

    result = "".join(reversed(text_parts)).strip()
    if not result:
        raise ValueError("Gemini returned no text output.")
    return result


async def assign_priority(subject, description):
    departments = [choice[0] for choice in StaffProfile.Department.choices]
    priorities = [choice[0] for choice in Ticket.Priority.choices]
    schema = {
        "type": "object",
        "properties": {
            "priority": {"type": "string", "enum": priorities},
            "department": {"type": "string", "enum": departments},
        },
        "required": ["priority", "department"],
    }
    prompt = (
        "Act as a support-ticket classifier. Assign a priority based on urgency "
        "and impact, and select the best matching department. Return only JSON "
        'matching {"priority":"Low|Medium|High","department":"Technical|Billing|General"}. '
        f"Subject: {subject}\nDescription: {description}"
    )
    result = json.loads(await _gemini_generate(prompt, schema))
    if (
        not isinstance(result, dict)
        or result.get("priority") not in priorities
        or result.get("department") not in departments
    ):
        raise ValueError("Gemini returned an invalid ticket classification.")
    return result


def _staff_profile_or_forbidden(user):
    profile = StaffProfile.objects.filter(user=user).first()
    if profile is None:
        return None, HttpResponseForbidden("A staff profile is required.")
    return profile, None


def _gemini_http_error_detail(error):
    response = error.response
    if response is None:
        return "No response details were returned."
    try:
        payload = response.json()
    except ValueError:
        content_type = response.headers.get("Content-Type", "unknown content type")
        body = " ".join(response.text.split())
        if settings.GEMINI_API_KEY:
            body = body.replace(settings.GEMINI_API_KEY, "[REDACTED]")
        if body:
            return f"Non-JSON response ({content_type}): {body[:300]}"
        return f"Gemini returned an empty non-JSON response ({content_type})."
    if not isinstance(payload, dict):
        return "Gemini returned an unexpected error response."
    error_payload = payload.get("error")
    if not isinstance(error_payload, dict):
        return "Gemini returned an unexpected error response."
    detail = error_payload.get("message")
    if not isinstance(detail, str) or not detail:
        return "Gemini did not include an error message."
    return detail[:500]


@login_required
def create_ticket(request):
    if request.method == "POST":
        form = TicketForm(request.POST)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.user = request.user
            try:
                classification = async_to_sync(assign_priority)(
                    ticket.subject, ticket.description
                )
            except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
                logger.exception("Could not classify ticket with Gemini.")
                messages.warning(
                    request,
                    "Your ticket was submitted, but automatic classification "
                    "was unavailable. It was saved with Medium priority.",
                )
            else:
                ticket.priority = classification["priority"]
                ticket.assigned_to = (
                    StaffProfile.objects.filter(
                        department=classification["department"]
                    )
                    .order_by("user__username")
                    .first()
                )
            ticket.save()
            messages.success(request, "Your support ticket was created.")
            return redirect("ticket_detail", ticket_id=ticket.pk)
    else:
        form = TicketForm()
    return render(request, "support/create_ticket.html", {"form": form})


@login_required
def ticket_detail(request, ticket_id):
    ticket = get_object_or_404(
        Ticket.objects.select_related("user", "assigned_to__user").prefetch_related(
            "messages__user"
        ),
        pk=ticket_id,
    )
    is_owner = ticket.user_id == request.user.pk
    is_assigned_staff = ticket.assigned_to_id and ticket.assigned_to.user_id == request.user.pk
    is_staff = StaffProfile.objects.filter(user=request.user).exists()
    if not (is_owner or is_assigned_staff or is_staff):
        return HttpResponseForbidden("You do not have access to this ticket.")

    if request.method == "POST":
        form = MessageForm(request.POST)
        if form.is_valid():
            message = form.save(commit=False)
            message.ticket = ticket
            message.user = request.user
            message.save()
            messages.success(request, "Your message was sent.")
            return redirect("ticket_detail", ticket_id=ticket.pk)
    else:
        form = MessageForm()
    return render(
        request,
        "support/ticket_detail.html",
        {"ticket": ticket, "form": form},
    )


@login_required(login_url="staff_login")
def staff_dashboard(request):
    profile, error_response = _staff_profile_or_forbidden(request.user)
    if error_response:
        return error_response
    tickets = Ticket.objects.filter(assigned_to__isnull=True).select_related("user")
    return render(
        request,
        "support/staff_dashboard.html",
        {"tickets": tickets, "staff_profile": profile},
    )


@login_required(login_url="staff_login")
@require_POST
def claim_ticket(request, ticket_id):
    profile, error_response = _staff_profile_or_forbidden(request.user)
    if error_response:
        return error_response

    updated = Ticket.objects.filter(
        pk=ticket_id, assigned_to__isnull=True
    ).update(assigned_to=profile)
    if updated:
        messages.success(request, "Ticket claimed successfully.")
    else:
        messages.error(request, "That ticket is no longer available to claim.")
    return redirect("staff_dashboard")


@login_required(login_url="staff_login")
def my_tickets(request):
    profile, error_response = _staff_profile_or_forbidden(request.user)
    if error_response:
        return error_response
    tickets = Ticket.objects.filter(assigned_to=profile).select_related("user")
    return render(
        request,
        "support/my_tickets.html",
        {"tickets": tickets, "staff_profile": profile},
    )


@login_required
@require_POST
async def get_faq_answer(request):
    try:
        if request.content_type == "application/json":
            data = json.loads(request.body)
        else:
            data = parse_qs(request.body.decode("utf-8"))
        question_value = data.get("question", "")
        question = (
            question_value[0]
            if isinstance(question_value, list) and question_value
            else question_value
        )
        question = question.strip() if isinstance(question, str) else ""
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
        return JsonResponse({"error": "Provide a valid question."}, status=400)

    if not question:
        return JsonResponse({"error": "Provide a valid question."}, status=400)

    faq_text = (
        "1. Account access: Use the password-reset link on the sign-in page. "
        "If you no longer have access to your email, submit a support ticket.\n"
        "2. Billing: Include the invoice number and account email in a billing "
        "ticket. Do not send full payment-card details.\n"
        "3. Technical issues: Include the steps to reproduce the issue and any "
        "error message in a technical ticket.\n"
        "4. Ticket updates: Sign in and open your ticket to read or send messages."
    )
    prompt = (
        "You are a helpful support agent. Answer the user's question clearly "
        "and concisely using only these FAQs. If they do not answer the question, "
        "say that a support ticket is needed; do not invent policy details.\n"
        f"FAQs:\n{faq_text}\n\nQuestion: {question}"
    )
    try:
        answer = await _gemini_generate(prompt)
    except GeminiConfigurationError as exc:
        logger.warning("FAQ answer unavailable: %s", exc)
        return JsonResponse(
            {"error": "FAQ answering is not configured on this server."},
            status=503,
        )
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        logger.error(
            "Gemini rejected the FAQ request (HTTP %s): %s",
            status_code,
            _gemini_http_error_detail(exc),
        )
        if status_code == 404:
            error = "The configured Gemini model is unavailable. Check GEMINI_MODEL."
        elif status_code in {400, 401, 403}:
            error = (
                "Gemini rejected the request. Check the API key, model, and API access."
            )
        else:
            error = "The FAQ service is temporarily unavailable."
        return JsonResponse({"error": error}, status=502)
    except requests.RequestException:
        logger.exception("Gemini FAQ request failed.")
        return JsonResponse(
            {
                "error": (
                    "Could not reach Gemini. Check the server connection and try again."
                )
            },
            status=502,
        )
    except (KeyError, IndexError, TypeError, ValueError):
        logger.exception("Gemini returned an invalid FAQ response.")
        return JsonResponse(
            {"error": "The FAQ service returned an invalid response."}, status=502
        )
    return JsonResponse({"answer": answer})
