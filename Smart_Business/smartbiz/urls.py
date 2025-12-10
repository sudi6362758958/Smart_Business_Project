# smartbiz/urls.py

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

# IMPORTANT: import dashboard views for the home alias
from dashboard import views as dashboard_views

urlpatterns = [
    path('admin/', admin.site.urls),

    path('', include('accounts.urls', namespace='accounts')),
    
    
    path('products/', include('products.urls', namespace='products')),
    path('sales/', include('sales.urls', namespace='sales')),
    path('purchases/', include('purchases.urls', namespace='purchases')),
    path('expenses/', include('expenses.urls', namespace='expenses')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
