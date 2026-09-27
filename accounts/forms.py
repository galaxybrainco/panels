from allauth.account.forms import SignupForm as BaseSignupForm
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

from actors.handles import validate_handle
from actors.models import Actor
from actors.services import create_local_actor


class SignupForm(BaseSignupForm):
    handle = forms.CharField(
        max_length=32,
        label="Handle",
        help_text=(
            "Your fediverse username: @handle@this-instance. Lowercase letters, "
            "digits, and underscores."
        ),
    )

    def clean_handle(self):
        handle = validate_handle(self.cleaned_data["handle"])
        if Actor.objects.filter(handle=handle, domain="").exists():
            raise ValidationError("That handle is already taken.")
        return handle

    def save(self, request):
        with transaction.atomic():
            user = super().save(request)
            create_local_actor(self.cleaned_data["handle"], user=user)
        return user
