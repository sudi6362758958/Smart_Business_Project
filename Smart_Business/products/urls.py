from django.urls import path
from . import views

app_name = "products"

urlpatterns = [
    path('', views.product_list, name='product_list'),
    path('create/', views.product_create, name='product_create'),
    path("<int:pk>/delete/", views.product_delete, name="product_delete"),
    path('api/price-calculator/', views.price_calculator_api, name='price_calculator_api'),
    # optional detail route if you use it
    path('<int:pk>/', views.product_detail, name='product_detail'),
]
