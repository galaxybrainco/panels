from allauth.account.views import SignupView as BaseSignupView
from django.db import IntegrityError


class SignupView(BaseSignupView):
    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except IntegrityError:
            form.add_error(
                "handle", "That handle was just taken. Please choose another."
            )
            return self.form_invalid(form)
