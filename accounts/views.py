from allauth.account.views import SignupView as BaseSignupView
from django.db import IntegrityError


class SignupView(BaseSignupView):
    def form_valid(self, form):
        from moderation import registration

        if registration.ip_blocked(self.request.META.get("REMOTE_ADDR", "")):
            form.add_error(None, "Signups from your network are not allowed.")
            return self.form_invalid(form)
        try:
            return super().form_valid(form)
        except IntegrityError:
            form.add_error(
                "handle", "That handle was just taken. Please choose another."
            )
            return self.form_invalid(form)
