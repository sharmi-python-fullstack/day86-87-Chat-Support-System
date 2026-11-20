import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the initial superuser from deployment environment variables."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")

        if not username and not email and not password:
            self.stdout.write("No bootstrap superuser credentials configured; skipping.")
            return
        if not username or not email or not password:
            raise CommandError(
                "Set DJANGO_SUPERUSER_USERNAME, DJANGO_SUPERUSER_EMAIL, and "
                "DJANGO_SUPERUSER_PASSWORD together."
            )

        user_model = get_user_model()
        username_field = user_model.USERNAME_FIELD
        lookup = {username_field: username}
        existing_user = user_model._default_manager.filter(**lookup).first()
        if existing_user is not None:
            if existing_user.is_superuser and existing_user.is_active:
                self.stdout.write("Bootstrap superuser already exists; leaving it unchanged.")
                return
            raise CommandError(
                f"An active superuser is not available for {username_field} "
                f"{username!r}; refusing to change the existing account."
            )

        candidate = user_model(**lookup)
        if hasattr(candidate, "email"):
            candidate.email = email
        try:
            validate_password(password, user=candidate)
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from error

        call_command(
            "createsuperuser",
            interactive=False,
            **{username_field: username, "email": email},
        )
