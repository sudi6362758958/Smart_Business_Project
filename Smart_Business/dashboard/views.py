from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models import Sum

from sales.models import Invoice
from purchases.models import Purchase
from expenses.models import Expense


@login_required
def home(request):
    """
    Dashboard home view.

    - Requires login via @login_required (Django will redirect to settings.LOGIN_URL).
    - Gets the first business associated with the user (if any) and calculates today's aggregates.
    """
    # request.user is guaranteed to be authenticated because of @login_required
    business = request.user.businesses.first()  # adjust if your relation name differs

    context = {}
    if business:
        today = timezone.localdate()

        today_sales = (
            Invoice.objects.filter(business=business, date=today)
            .aggregate(total=Sum('total'))['total'] or 0
        )
        today_purchases = (
            Purchase.objects.filter(business=business, date=today)
            .aggregate(total=Sum('total'))['total'] or 0
        )
        today_expenses = (
            Expense.objects.filter(business=business, date=today)
            .aggregate(total=Sum('amount'))['total'] or 0
        )

        context.update({
            'business': business,
            'today_sales': today_sales,
            'today_purchases': today_purchases,
            'today_expenses': today_expenses,
        })

    return render(request, 'dashboard/home.html', context)
