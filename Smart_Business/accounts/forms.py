from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from .models import Business
from django.contrib.auth import authenticate


class BusinessRegistrationForm(forms.ModelForm):
    owner_name = forms.CharField(
        required=False,
        label="Owner Name",
        widget=forms.TextInput(attrs={"placeholder": "Owner full name"})
    )
    # Extra fields for owner creation (only required when creating new owner)
    owner_email = forms.EmailField(required=False, help_text="Owner login email (if you are not logged in).")
    password = forms.CharField(required=False, widget=forms.PasswordInput, help_text="Password for owner account.")
    confirm_password = forms.CharField(required=False, widget=forms.PasswordInput, label="Confirm password")

    class Meta:
        model = Business
        # REQUIRED CHANGE: include owner_name & owner_email so they save to model
        fields = ['owner_name', 'owner_email', 'name', 'email', 'phone', 'gst_number', 'address']

    def __init__(self, *args, **kwargs):
        # accept an optional user kwarg to indicate a logged-in user
        self.request_user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        # If user is authenticated, owner fields are not required
        if self.request_user and self.request_user.is_authenticated:
            self.fields['owner_email'].required = False
            self.fields['password'].required = False
            self.fields['confirm_password'].required = False
            self.fields['owner_name'].required = False
        else:
            # For anonymous users, require owner_email, owner_name and passwords
            self.fields['owner_email'].required = True
            self.fields['password'].required = True
            self.fields['confirm_password'].required = True
            self.fields['owner_name'].required = True

    def clean_owner_email(self):
        email = self.cleaned_data.get('owner_email')
        # if user is anonymous, email must not already exist
        if (not self.request_user or not self.request_user.is_authenticated) and email:
            if User.objects.filter(email__iexact=email).exists():
                raise ValidationError("A user with this email already exists. Please login or use a different email.")
        return email

    def clean(self):
        cleaned = super().clean()
        pwd = cleaned.get('password')
        pwd2 = cleaned.get('confirm_password')

        # only validate passwords for anonymous/new-user flow
        if (not self.request_user or not self.request_user.is_authenticated):
            # password presence / matching / length checks -> attach to fields where possible
            if not pwd:
                self.add_error('password', "Password is required to create owner account.")
            if not pwd2:
                self.add_error('confirm_password', "Confirm Password is required to create owner account.")
            if pwd and pwd2 and pwd != pwd2:
                self.add_error('confirm_password', "Passwords do not match.")
            if pwd and len(pwd) < 6:
                self.add_error('password', "Password must be at least 6 characters long.")

        return cleaned


class EmailLoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "placeholder": "you@example.com",
            "autocomplete": "email"
        }),
        label="Email"
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "Enter your password",
            "autocomplete": "current-password"
        }),
        label="Password"
    )

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        self.user_cache = None

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get('email')
        password = cleaned.get('password')

        if email and password:
            try:
                user = User.objects.get(email__iexact=email)
            except User.DoesNotExist:
                raise ValidationError("Invalid email or password.")

            user = authenticate(self.request, username=user.username, password=password)
            if user is None:
                raise ValidationError("Invalid email or password.")
            if not user.is_active:
                raise ValidationError("This account is inactive. Please wait for approval or contact admin.")

            self.user_cache = user

        return cleaned

    def get_user(self):
        return self.user_cache


class BusinessApprovalForm(forms.ModelForm):
    class Meta:
        model = Business
        fields = ['status', 'is_approved']
