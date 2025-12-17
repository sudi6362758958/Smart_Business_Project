# accounts/models.py
from django.db import models
from django.contrib.auth.models import User
from django.core.validators import RegexValidator
from django.utils import timezone
from django.conf import settings


from django.conf import settings
from django.db import models
from django.core.validators import RegexValidator
from django.utils import timezone

class Business(models.Model):
    # owner user: nullable because anonymous registration may create business before a User exists
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='businesses'
    )

    # Owner contact info (stored for display/communication). Do NOT store passwords here.
    owner_name = models.CharField(max_length=255, blank=True, help_text="Owner full name ")
    owner_email = models.EmailField(blank=True, help_text="Owner email (used for login once activated)")

    # Business fields
    name = models.CharField(max_length=255, help_text="Business name")
    email = models.EmailField(blank=True, help_text="Business contact email")
    phone = models.CharField(
        max_length=30,
        blank=True,
        help_text="Business phone number",
        validators=[RegexValidator(r'^[0-9+\-\s()]*$', "Enter a valid phone number.")]
    )
    gst_number = models.CharField(max_length=64, blank=True, help_text="GST number (if applicable)")
    address = models.TextField(blank=True, help_text="Business address")

    # Admin workflow fields
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    is_approved = models.BooleanField(default=False)

    # meta fields
    views = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Business"
        verbose_name_plural = "Businesses"

    def __str__(self):
        return f"{self.name} ({self.owner_email or 'no owner email'})"

    def approve(self, activate_owner=True):
        """
        Mark business as approved. If the Business is linked to a User and activate_owner=True,
        activate the owner account so they can log in.
        """
        self.status = self.STATUS_APPROVED
        self.is_approved = True
        self.save(update_fields=['status', 'is_approved', 'updated_at'])

        if activate_owner and self.owner:
            if not self.owner.is_active:
                self.owner.is_active = True
                self.owner.save(update_fields=['is_active'])

    def reject(self, deactivate_owner=False):
        """
        Mark business as rejected. Optionally deactivate the linked owner account.
        """
        self.status = self.STATUS_REJECTED
        self.is_approved = False
        self.save(update_fields=['status', 'is_approved', 'updated_at'])

        if deactivate_owner and self.owner and self.owner.is_active:
            self.owner.is_active = False
            self.owner.save(update_fields=['is_active'])

    def increment_views(self, by=1):
        self.views = (self.views or 0) + by
        self.save(update_fields=['views'])

    def clean(self):
        """
        Ensure consistency: if an owner User is set, prefer using their name/email.
        (This method does not replace more advanced validation you may want.)
        """
        if self.owner:
            # Example: prefer owner email over owner_email if owner exists
            if not self.owner_email:
                self.owner_email = (self.owner.email or '')  # sync for display
            if not self.owner_name:
                self.owner_name = f"{self.owner.get_full_name() or self.owner.username}"
