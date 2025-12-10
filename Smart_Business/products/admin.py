# Market/products/admin.py
from django.contrib import admin
from .models import Product, Category
from .forms import ProductForm


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "business")
    search_fields = ("name",)
    list_filter = ("business",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductForm
    list_display = ("name", "business", "category", "base_unit", "price_per_unit", "stock_qty")
    list_filter = ("base_unit", "category")
    search_fields = ("name",)
