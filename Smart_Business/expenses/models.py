from django.db import models
from accounts.models import Business
from django.utils import timezone
from decimal import Decimal

class Expense(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="expenses")
    name = models.CharField(max_length=255)     # e.g., "Electricity Bill"
    category = models.CharField(max_length=100, blank=True, null=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    date = models.DateField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.name} - {self.amount}"
