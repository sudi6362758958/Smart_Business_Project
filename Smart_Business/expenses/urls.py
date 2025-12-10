from django.urls import path
from . import views

app_name = "expenses"

urlpatterns = [
    path("", views.expense_list, name="expense_list"),
    path("create/", views.expense_create, name="expense_create"),
    path("<int:pk>/edit/", views.expense_edit, name="expense_edit"),
    path("<int:pk>/delete/", views.expense_delete, name="expense_delete"),

    # exports
    path("export/xlsx/", views.expense_export_xlsx, name="expense_export_xlsx"),
    path("export/pdf/", views.expense_export_pdf, name="expense_export_pdf"),
]
