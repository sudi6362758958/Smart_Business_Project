from django.contrib import admin
from .models import Business

@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'email', 'is_approved', 'created_at')
    list_filter = ('is_approved',)

