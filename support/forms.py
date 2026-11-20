from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .models import Message, StaffProfile, Ticket


class ClientAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if StaffProfile.objects.filter(user=user).exists():
            raise ValidationError(
                "Please use the staff sign-in page for this account.",
                code="staff_account",
            )


class StaffAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not StaffProfile.objects.filter(user=user).exists():
            raise ValidationError(
                "This account is not registered as staff.",
                code="not_staff",
            )


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ("subject", "description")
        widgets = {
            "subject": forms.TextInput(attrs={"autocomplete": "off"}),
            "description": forms.Textarea(attrs={"rows": 5}),
        }


class MessageForm(forms.ModelForm):
    class Meta:
        model = Message
        fields = ("content",)
        labels = {"content": "Your message"}
        widgets = {
            "content": forms.Textarea(attrs={"rows": 3, "placeholder": "Write a message..."})
        }
