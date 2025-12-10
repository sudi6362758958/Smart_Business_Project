from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

app_name = 'accounts'

urlpatterns = [
    # Public home at root of site (because project urls include accounts at '')
    path('', views.home, name='home'),

    # Login / Logout (LoginView uses your template at templates/accounts/login.html)
    path('login/', auth_views.LoginView.as_view(template_name='accounts/login.html'), name='login'),

    path('logout/', views.logout_view, name='logout'),
    # Post-login redirect helper
    path('post-login/', views.post_login, name='post_login'),

    # Dashboards
    path("dashboard/<int:business_id>/", views.owner_dashboard, name="owner_dashboard"),
    path('dashboard/admin/', views.admin_dashboard, name='admin_dashboard'),

    # Business CRUD
    path('business/register/', views.register_business, name='register_business'),
    path('business/<int:pk>/', views.business_detail, name='business_detail'),
    path('business/<int:pk>/approve/', views.approve_business, name='approve_business'),
    path('business/<int:pk>/reject/', views.reject_business, name='reject_business'),

    # Optional edits
    path('business/<int:pk>/edit/', views.edit_business, name='edit_business'),
    path('business/<int:pk>/delete/', views.delete_business, name='delete_business'),    
    
    path('export/csv/', views.export_csv, name='export_csv'),
    path('export/pdf/', views.export_pdf, name='export_pdf'),

    
    path("dashboard/<int:business_id>/export_pdf/", views.owner_dashboard_export_pdf, name="owner_dashboard_export_pdf"),
    path("dashboard/<int:business_id>/export_csv/", views.owner_dashboard_export_csv, name="owner_dashboard_export_csv"),
    
]
