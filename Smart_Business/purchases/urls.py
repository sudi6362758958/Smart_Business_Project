# purchases/urls.py
from django.urls import path
from . import views

app_name = "purchases"

urlpatterns = [
    path("", views.purchase_list, name="purchase_list"),
    path("create/", views.purchase_create, name="purchase_create"),
    path("<int:pk>/edit/", views.purchase_edit, name="purchase_edit"),
    path("<int:pk>/", views.purchase_detail, name="purchase_detail"),
    path("<int:pk>/delete/", views.purchase_delete, name="purchase_delete"),
    
    path('export/', views.purchase_export, name='purchase_export'),
]
