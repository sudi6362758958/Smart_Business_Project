from django.contrib import admin
from .models import Purchase, PurchaseItem

class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem

@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    inlines = [PurchaseItemInline]
