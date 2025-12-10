from django.urls import path
from . import views

app_name = "sales"

urlpatterns = [
    path("", views.sales_list, name="sales_list"),
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/create/", views.invoice_create, name="invoice_create"),
    path(
        "invoices/<int:pk>/edit/", views.invoice_create, name="invoice_edit"
    ),  # Changed to invoice_create
    path("invoices/<int:pk>/delete/", views.invoice_delete, name="invoice_delete"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("customers/create/", views.customer_create, name="customer_create"),
    path(
        "customers/<int:pk>/json/",
        views.customer_detail_json,
        name="customer_detail_json",
    ),
]
