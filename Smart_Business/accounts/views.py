from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.contrib.auth import logout as django_logout
from django.views.decorators.http import require_POST
from django.db import transaction, IntegrityError
from django.contrib.auth.models import User
from .forms import BusinessRegistrationForm

from .models import Business
from .forms import BusinessRegistrationForm, EmailLoginForm
from datetime import date, datetime, timedelta
from xhtml2pdf import pisa
import io
from sales.models import Invoice
from purchases.models import Purchase
from expenses.models import Expense


def home(request):
    """
    Public homepage displayed at root ('/').
    NOTE: This will NOT redirect authenticated users — it always renders home.html.
    """
    context = {
        'total_businesses': Business.objects.count(),
        'pending_businesses': Business.objects.filter(status=Business.STATUS_PENDING).count(),
        'approved_businesses': Business.objects.filter(status=Business.STATUS_APPROVED).count(),
        'recent_businesses': Business.objects.order_by('-created_at')[:10],
    }
    return render(request, 'accounts/home.html', context)


@login_required
def post_login(request):
    user = request.user

    # Admin or superuser → admin dashboard
    if user.is_superuser:
        return redirect('accounts:admin_dashboard')

    # Owner must have a business
    try:
        business = Business.objects.get(owner=user)
    except Business.DoesNotExist:
        # fallback if no business yet
        return redirect('accounts:home')

    # Redirect owner to their dashboard with business_id
    return redirect('accounts:owner_dashboard', business_id=business.id)


@login_required
def owner_dashboard(request):
    """
    Owner dashboard showing businesses that belong to the logged-in user.
    """
    businesses = Business.objects.filter(owner=request.user).order_by('-created_at')
    context = {
        'businesses': businesses,
    }
    return render(request, 'accounts/owner/owner_dashboard.html', context)


@login_required
def admin_dashboard(request):
    """
    Admin dashboard. Only accessible by superuser.
    """
    if not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect('accounts:owner/owner_dashboard')

    total = Business.objects.count()
    pending_count = Business.objects.filter(status=Business.STATUS_PENDING).count()
    approved_count = Business.objects.filter(status=Business.STATUS_APPROVED).count()
    businesses = Business.objects.all().order_by('-created_at')

    context = {
        'total': total,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'businesses': businesses,
    }
    return render(request, 'accounts/admin/admin_dashboard.html', context)


@login_required
def business_detail(request, pk):
    """
    Show details for a business and increment views if the viewer is not the owner.
    """
    business = get_object_or_404(Business, pk=pk)

    if request.user != business.owner:
        business.views = (business.views or 0) + 1
        business.save(update_fields=['views'])

    return render(request, 'accounts/business_detail.html', {'business': business})



from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponseForbidden
from django.contrib import messages
from django.urls import reverse, NoReverseMatch

# import your form and model
from .forms import BusinessRegistrationForm
from .models import Business

def _make_unique_username(base):
    """
    Create a username slug from the base (typically email local-part) and
    ensure uniqueness by appending a counter if needed.
    """
    username = base
    counter = 0
    while User.objects.filter(username=username).exists():
        counter += 1
        username = f"{base}{counter}"
    return username


def register_business(request, pk=None):
    """
    Create or edit a Business.

    - Admin registering/editing: Back -> admin_dashboard
    - Owner registering/editing: Back -> owner_dashboard (for that business)
    - Others/anonymous: Back -> accounts:home
    """
    edit = bool(pk)
    business = None

    if edit:
        business = get_object_or_404(Business, pk=pk)

        # permission rules for editing
        if business.owner:
            if not request.user.is_authenticated:
                return HttpResponseForbidden("You must be logged in to edit this business.")
            if business.owner != request.user and not request.user.is_staff and not request.user.is_superuser:
                return HttpResponseForbidden("You are not allowed to edit this business.")
        else:
            if not request.user.is_authenticated and not request.user.is_staff:
                return HttpResponseForbidden("Login required to edit this unowned business.")

    # Decide back_url BEFORE form rendering
    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        # admin
        back_url = reverse('accounts:admin_dashboard')
    elif request.user.is_authenticated and edit and business:
        # owner editing existing business
        back_url = reverse('accounts:owner_dashboard', args=[business.id])
    else:
        # anonymous or new business without owner dashboard yet
        back_url = reverse('accounts:home')

    if request.method == 'POST':
        form = BusinessRegistrationForm(
            request.POST,
            request.FILES,
            user=request.user,
            instance=(business if edit else None),
        )

        if form.is_valid():
            new_business = form.save(commit=False)

            if request.user.is_authenticated:
                # logged-in user becomes owner
                new_business.owner = request.user
            else:
                # anonymous flow: same as you had before
                owner_email = form.cleaned_data.get('owner_email')
                owner_name = form.cleaned_data.get('owner_name')
                pwd = form.cleaned_data.get('password')

                if owner_email and pwd:
                    try:
                        with transaction.atomic():
                            local_part = owner_email.split('@', 1)[0]
                            username = _make_unique_username(local_part)
                            created_user = User.objects.create_user(
                                username=username,
                                email=owner_email,
                                password=pwd,
                            )
                            created_user.is_active = False
                            created_user.first_name = owner_name or created_user.first_name
                            created_user.save(update_fields=['is_active', 'first_name'])
                            new_business.owner = created_user
                    except IntegrityError:
                        messages.error(request, "Could not create owner account — please try again or contact admin.")
                        return render(
                            request,
                            'accounts/register_business.html',
                            {'form': form, 'edit': edit, 'business': business, 'back_url': back_url},
                        )
                else:
                    new_business.owner = getattr(business, 'owner', None)

            new_business.is_approved = False
            new_business.status = new_business.STATUS_PENDING

            new_business.save()
            try:
                form.save_m2m()
            except Exception:
                pass

            messages.success(request, "Business saved successfully. The admin will review and approve it shortly.")

            # redirect after save
            if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
                return redirect('accounts:admin_dashboard')
            elif new_business.owner:
                try:
                    return redirect('accounts:owner_dashboard', new_business.id)
                except NoReverseMatch:
                    return redirect('accounts:home')
            else:
                return redirect('accounts:home')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = BusinessRegistrationForm(
            instance=(business if edit else None),
            user=request.user,
        )

    context = {
        'form': form,
        'edit': edit,
        'business': business,
        'back_url': back_url,
    }
    return render(request, 'accounts/register_business.html', context)


@require_POST
@login_required
def approve_business(request, pk):
    """
    Approve a business (admin only). POST-only.
    When approved, activate the owner user account so they can log in.
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden("You don't have permission to approve businesses.")

    business = get_object_or_404(Business, pk=pk)
    business.approve()  # assumes model method toggles status/is_approved and saves
    # Ensure owner's user account is active after approval
    owner = business.owner
    if owner and not owner.is_active:
        owner.is_active = True
        owner.save(update_fields=['is_active'])

    messages.success(request, f"Business '{business.name}' approved and owner account activated.")
    return redirect('accounts:admin_dashboard')


@require_POST
@login_required
def reject_business(request, pk):
    """
    Reject a business (admin only). POST-only.
    Optionally keep the user inactive so they cannot log in.
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden("You don't have permission to reject businesses.")

    business = get_object_or_404(Business, pk=pk)
    business.reject()  # assumes model method toggles status/is_approved and saves

    # Optionally deactivate owner account on rejection (if desired)
    owner = business.owner
    if owner and owner.is_active:
        owner.is_active = False
        owner.save(update_fields=['is_active'])

    messages.success(request, f"Business '{business.name}' rejected.")
    return redirect('accounts:admin_dashboard')


@login_required
def edit_business(request, pk):
    """
    Allow the owner to edit their business. Admins/staff can also edit.
    """
    business = get_object_or_404(Business, pk=pk)

    # permission check
    if request.user != business.owner and not request.user.is_staff and not request.user.is_superuser:
        return HttpResponseForbidden("You don't have permission to edit this business.")

    # decide where Back/Cancel should go
    if request.user.is_staff or request.user.is_superuser:
        back_url = reverse('accounts:admin_dashboard')
    else:
        # owner
        back_url = reverse('accounts:owner_dashboard', args=[business.id])

    if request.method == 'POST':
        form = BusinessRegistrationForm(
            request.POST,
            request.FILES,
            instance=business,
            user=request.user
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Business updated.")

            # redirect after save
            if request.user.is_staff or request.user.is_superuser:
                return redirect('accounts:admin_dashboard')
            else:
                return redirect('accounts:owner_dashboard', business.id)
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = BusinessRegistrationForm(instance=business, user=request.user)

    context = {
        'form': form,
        'edit': True,
        'business': business,
        'back_url': back_url,
    }
    return render(request, 'accounts/register_business.html', context)


@login_required
def delete_business(request, pk):
    """
    Admin-only deletion of a Business.
    Only accepts POST. Non-staff users and non-POST requests are rejected.
    """
    # Only allow POST to perform destructive action
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    # Require staff/admin privileges
    if not request.user.is_authenticated or not request.user.is_staff:
        return HttpResponseForbidden("You do not have permission to delete businesses.")

    business = get_object_or_404(Business, pk=pk)

    # Optionally: capture owner and/or send notifications before deletion
    try:
        with transaction.atomic():
            # If you want to keep a log, create audit entry here before delete.
            business.delete()
        messages.success(request, f"Business '{business.name}' (ID: {pk}) was deleted successfully.")
    except IntegrityError:
        messages.error(request, "Could not delete the business due to a database error. Try again or contact admin.")
    except Exception as exc:
        # Generic fallback; log the exception in production
        messages.error(request, f"Failed to delete business: {exc}")

    # Redirect back to admin dashboard (fallback to home if reverse fails)
    try:
        return redirect(reverse('accounts:admin_dashboard'))
    except NoReverseMatch:
        return redirect('home')


def logout_view(request):
    """
    Accepts GET or POST. Logs out and redirects to accounts:home.
    (Convenient for clicking logout links; change to POST-only for production.)
    """
    if request.method not in ('GET', 'POST'):
        return HttpResponseNotAllowed(['GET', 'POST'])
    django_logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('accounts:home')



def login_view(request):
    if request.method == 'POST':
        form = EmailLoginForm(request.POST, request=request)
        if form.is_valid():
            login(request, form.get_user())
            return redirect('accounts:post_login')
    else:
        form = EmailLoginForm()
    return render(request, 'accounts/login.html', {'form': form})




# accounts/views.py (replace the owner_dashboard function with this)
# accounts/views.py additions/replace (imports)
from io import BytesIO
from datetime import timedelta, datetime
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.utils.timezone import now
from django.http import HttpResponse, HttpResponseBadRequest

# WeasyPrint for prettier PDF

import csv
import io
from sales.models import Invoice
from purchases.models import Purchase
from expenses.models import Expense
import pdfkit
from django.http import HttpResponse
from django.template.loader import render_to_string



@login_required
def owner_dashboard(request, business_id):
    """
    Dashboard with optional date-range selector.
    Query params:
      - start_date (YYYY-MM-DD)
      - end_date   (YYYY-MM-DD)
    If no range provided, the page shows:
      - week_rows for current week (Mon-Sun)
      - month_rows for month-to-date
    But if start_date & end_date provided, we compute rows for that range (inclusive).
    """
    business = get_object_or_404(Business, pk=business_id, owner=request.user)
    today = now().date()

    # date-range from query params (optional)
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")
    start_date = None
    end_date = None
    try:
        if start_date_str:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        if end_date_str:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except Exception:
        start_date = None
        end_date = None

    # ---- week_rows & month_rows (as before) ----
    # Week (Mon..Sun)
    start_week = today - timedelta(days=today.weekday())
    week_rows = []
    week_labels = []
    week_sales_arr = []
    week_purchases_arr = []
    week_expenses_arr = []
    week_profit_arr = []

    for i in range(7):
        d = start_week + timedelta(days=i)
        week_labels.append(d.strftime("%a %d"))
        s = Invoice.objects.filter(business=business, date=d).aggregate(total=Sum("total"))["total"] or 0
        p = Purchase.objects.filter(business=business, date=d).aggregate(total=Sum("total"))["total"] or 0
        e = Expense.objects.filter(business=business, date=d).aggregate(total=Sum("amount"))["total"] or 0
        profit = (s or 0) - ((p or 0) + (e or 0))
        row = {"date": d.strftime("%Y-%m-%d"), "label": d.strftime("%a %d"),
               "sales": float(s), "purchases": float(p), "expenses": float(e), "profit": float(profit)}
        week_rows.append(row)
        week_sales_arr.append(float(s)); week_purchases_arr.append(float(p))
        week_expenses_arr.append(float(e)); week_profit_arr.append(float(profit))

    # Month (1..today.day)
    month_rows = []
    month_labels = []
    month_sales_arr = []
    month_purchases_arr = []
    month_expenses_arr = []
    month_profit_arr = []

    for dnum in range(1, today.day + 1):
        d = today.replace(day=dnum)
        month_labels.append(str(dnum))
        s = Invoice.objects.filter(business=business, date=d).aggregate(total=Sum("total"))["total"] or 0
        p = Purchase.objects.filter(business=business, date=d).aggregate(total=Sum("total"))["total"] or 0
        e = Expense.objects.filter(business=business, date=d).aggregate(total=Sum("amount"))["total"] or 0
        profit = (s or 0) - ((p or 0) + (e or 0))
        row = {"day": dnum, "date": d.strftime("%Y-%m-%d"),
               "sales": float(s), "purchases": float(p), "expenses": float(e), "profit": float(profit)}
        month_rows.append(row)
        month_sales_arr.append(float(s)); month_purchases_arr.append(float(p))
        month_expenses_arr.append(float(e)); month_profit_arr.append(float(profit))

    # Today quick numbers
    today_sales = Invoice.objects.filter(business=business, date=today).aggregate(total=Sum("total"))["total"] or 0
    today_purchase = Purchase.objects.filter(business=business, date=today).aggregate(total=Sum("total"))["total"] or 0
    today_expense = Expense.objects.filter(business=business, date=today).aggregate(total=Sum("amount"))["total"] or 0
    today_profit = (today_sales or 0) - ((today_purchase or 0) + (today_expense or 0))

    # categories & filtered_total for expenses list
    categories = Expense.objects.filter(business=business).order_by("category").values_list("category", flat=True).distinct()
    filtered_total = Expense.objects.filter(business=business).aggregate(total_amount=Sum("amount"))["total_amount"] or 0

    # If user provided a custom range, compute a per-day list for that range (for export/preview)
    custom_rows = []
    if start_date and end_date and start_date <= end_date:
        cur = start_date
        while cur <= end_date:
            s = Invoice.objects.filter(business=business, date=cur).aggregate(total=Sum("total"))["total"] or 0
            p = Purchase.objects.filter(business=business, date=cur).aggregate(total=Sum("total"))["total"] or 0
            e = Expense.objects.filter(business=business, date=cur).aggregate(total=Sum("amount"))["total"] or 0
            profit = (s or 0) - ((p or 0) + (e or 0))
            custom_rows.append({"date": cur.strftime("%Y-%m-%d"), "sales": float(s), "purchases": float(p), "expenses": float(e), "profit": float(profit)})
            cur = cur + timedelta(days=1)

    context = {
        "business": business,
        "today_sales": today_sales, "today_purchase": today_purchase, "today_expense": today_expense, "today_profit": today_profit,
        "week_rows": week_rows, "week_labels": week_labels, "week_sales": week_sales_arr, "week_purchases": week_purchases_arr,
        "week_expenses": week_expenses_arr, "week_profit": week_profit_arr,
        "month_rows": month_rows, "month_labels": month_labels, "month_sales": month_sales_arr,
        "month_purchases": month_purchases_arr, "month_expenses": month_expenses_arr, "month_profit": month_profit_arr,
        "categories": [c for c in categories if c], "filtered_total": filtered_total,
        "chart_labels": month_labels, "chart_data": month_sales_arr,
        # custom range preview
        "custom_rows": custom_rows,
        "start_date": start_date_str or "",
        "end_date": end_date_str or "",
    }

    return render(request, "accounts/owner/owner_dashboard.html", context)



# accounts/views.py (append/replace the existing export functions)
import csv
import io
from decimal import Decimal
from datetime import date, datetime, timedelta

from django.apps import apps
from django.db.models import Sum, DateField, DateTimeField, DecimalField, FloatField
from django.http import HttpResponse, Http404
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404
from django.templatetags.static import static

from xhtml2pdf import pisa

from .models import Business  # keep your Business import

# ---------------- Model discovery helpers (robust) ----------------

def _find_model_by_names(names):
    for name in names:
        if '.' in name:
            app_label, model_name = name.split('.', 1)
            try:
                m = apps.get_model(app_label, model_name)
                if m:
                    return m
            except LookupError:
                pass
    lower = {n.lower() for n in names}
    for m in apps.get_models():
        if m.__name__.lower() in lower:
            return m
    for n in names:
        for app_conf in apps.get_app_configs():
            try:
                m = apps.get_model(app_conf.label, n)
                if m:
                    return m
            except LookupError:
                continue
    return None

def _choose_date_field(model):
    candidates = ('date', 'created_at', 'payment_date', 'invoice_date', 'posted_at')
    for name in candidates:
        try:
            f = model._meta.get_field(name)
            if isinstance(f, (DateField, DateTimeField)):
                return name
        except Exception:
            pass
    for f in model._meta.get_fields():
        try:
            if isinstance(f, (DateField, DateTimeField)):
                return f.name
        except Exception:
            pass
    raise LookupError(f"No date/datetime field found on model {model.__name__}")

def _choose_amount_field(model):
    candidates = ('total', 'amount', 'line_total', 'grand_total', 'invoice_total')
    for name in candidates:
        try:
            f = model._meta.get_field(name)
            if isinstance(f, (DecimalField, FloatField)):
                return name
        except Exception:
            pass
    for f in model._meta.get_fields():
        try:
            if isinstance(f, (DecimalField, FloatField)):
                return f.name
        except Exception:
            pass
    raise LookupError(f"No numeric amount field found on model {model.__name__}")

# Try to resolve common models (adjust names if your project differs)
SalesModel = _find_model_by_names(['market.sales.Invoice', 'Invoice', 'Sale', 'Sales'])
PurchaseModel = _find_model_by_names(['purchases.Purchase', 'Purchase', 'Purchases', 'market.Purchase'])
ExpenseModel = _find_model_by_names(['expenses.Expense', 'Expense', 'Expenses', 'market.Expense'])

# ---------------- Period and aggregation helpers ----------------

def _parse_period(request):
    period = request.GET.get('period', 'week')
    today = date.today()
    if period == 'month':
        start = today - timedelta(days=29)
        end = today
    elif period == 'range':
        s = request.GET.get('start_date')
        e = request.GET.get('end_date')
        try:
            start = datetime.strptime(s, '%Y-%m-%d').date() if s else None
            end = datetime.strptime(e, '%Y-%m-%d').date() if e else None
        except Exception:
            raise Http404("Invalid start_date or end_date format (use YYYY-MM-DD).")
        if not start or not end:
            raise Http404("start_date and end_date required for period=range")
        if end < start:
            start, end = end, start
    else:
        start = today - timedelta(days=6)
        end = today
    return start, end

def _daily_aggregates(business, start, end):
    """
    returns list of dicts with Decimal numbers:
    [{'date': date, 'sales': Decimal, 'purchases': Decimal, 'expenses': Decimal, 'profit': Decimal}, ...]
    """
    if not SalesModel:
        raise Http404("Sales (Invoice) model not found. Adjust model discovery in views.py.")

    # sales
    sales_date_field = _choose_date_field(SalesModel)
    sales_amount_field = _choose_amount_field(SalesModel)
    sales_qs = (
        SalesModel.objects
        .filter(**{'business': business, f"{sales_date_field}__gte": start, f"{sales_date_field}__lte": end})
        .values(sales_date_field)
        .annotate(total=Sum(sales_amount_field))
    )
    sales_by_date = {r[sales_date_field]: (Decimal(r['total'] or 0)) for r in sales_qs}

    # purchases
    purch_by_date = {}
    if PurchaseModel:
        purch_date_field = _choose_date_field(PurchaseModel)
        purch_amount_field = _choose_amount_field(PurchaseModel)
        purch_qs = (
            PurchaseModel.objects
            .filter(**{'business': business, f"{purch_date_field}__gte": start, f"{purch_date_field}__lte": end})
            .values(purch_date_field)
            .annotate(total=Sum(purch_amount_field))
        )
        purch_by_date = {r[purch_date_field]: (Decimal(r['total'] or 0)) for r in purch_qs}

    # expenses
    exp_by_date = {}
    if ExpenseModel:
        exp_date_field = _choose_date_field(ExpenseModel)
        exp_amount_field = _choose_amount_field(ExpenseModel)
        exp_qs = (
            ExpenseModel.objects
            .filter(**{'business': business, f"{exp_date_field}__gte": start, f"{exp_date_field}__lte": end})
            .values(exp_date_field)
            .annotate(total=Sum(exp_amount_field))
        )
        exp_by_date = {r[exp_date_field]: (Decimal(r['total'] or 0)) for r in exp_qs}

    # build rows day by day (ensure Decimal arithmetic)
    rows = []
    cur = start
    while cur <= end:
        s = sales_by_date.get(cur, Decimal('0.00'))
        p = purch_by_date.get(cur, Decimal('0.00'))
        x = exp_by_date.get(cur, Decimal('0.00'))
        profit = (Decimal(s) - Decimal(p) - Decimal(x))
        rows.append({
            'date': cur,
            'sales': Decimal(s),
            'purchases': Decimal(p),
            'expenses': Decimal(x),
            'profit': Decimal(profit),
        })
        cur += timedelta(days=1)
    return rows

# ---------------- CSV export ----------------

def owner_dashboard_export_csv(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    if not (request.user.is_superuser or getattr(business, 'owner', None) == request.user):
        return HttpResponse('Forbidden', status=403)

    start, end = _parse_period(request)
    rows = _daily_aggregates(business, start, end)

    total_sales = sum((r['sales'] for r in rows), Decimal('0.00'))
    total_purchases = sum((r['purchases'] for r in rows), Decimal('0.00'))
    total_expenses = sum((r['expenses'] for r in rows), Decimal('0.00'))
    total_profit = sum((r['profit'] for r in rows), Decimal('0.00'))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Sales', 'Purchases', 'Expenses', 'Profit'])
    for r in rows:
        writer.writerow([
            r['date'].isoformat(),
            f"{r['sales']:.2f}",
            f"{r['purchases']:.2f}",
            f"{r['expenses']:.2f}",
            f"{r['profit']:.2f}",
        ])
    writer.writerow([])
    writer.writerow(['Totals', f"{total_sales:.2f}", f"{total_purchases:.2f}", f"{total_expenses:.2f}", f"{total_profit:.2f}"])

    csv_text = output.getvalue()
    output.close()

    filename = f"owner_dashboard_{business_id}_{start.isoformat()}_to_{end.isoformat()}.csv"
    response = HttpResponse(csv_text, content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

# ---------------- PDF export ----------------

def owner_dashboard_export_pdf(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    if not (request.user.is_superuser or getattr(business, 'owner', None) == request.user):
        return HttpResponse('Forbidden', status=403)

    start, end = _parse_period(request)
    rows = _daily_aggregates(business, start, end)

    total_sales = sum((r['sales'] for r in rows), Decimal('0.00'))
    total_purchases = sum((r['purchases'] for r in rows), Decimal('0.00'))
    total_expenses = sum((r['expenses'] for r in rows), Decimal('0.00'))
    total_profit = sum((r['profit'] for r in rows), Decimal('0.00'))

    # owner name and logo
    owner_obj = getattr(business, 'owner', None)
    owner_name = (owner_obj.get_full_name() if hasattr(owner_obj, 'get_full_name') else getattr(owner_obj, 'username', '')) if owner_obj else ''

    logo_url = None
    try:
        logo_url = request.build_absolute_uri(static('img/logo.png'))
    except Exception:
        logo_url = None

    context = {
        'business': business,
        'owner_name': owner_name,
        'rows': rows,
        'start': start,
        'end': end,
        'generated_at': datetime.now().strftime("%b %d, %Y, %I:%M %p"),
        'total_sales': f"{total_sales:.2f}",
        'total_purchases': f"{total_purchases:.2f}",
        'total_expenses': f"{total_expenses:.2f}",
        'total_profit': f"{total_profit:.2f}",
    }

    html = render_to_string('pdf/owner_dashboard.html', context=context, request=request)
    result = io.BytesIO()
    pisa_status = pisa.CreatePDF(src=html, dest=result)
    if pisa_status.err:
        # for debugging you can return the rendered HTML:
        return HttpResponse(html, content_type='text/html')

    pdf = result.getvalue()
    result.close()

    filename = f"owner_dashboard_{business_id}_{start.isoformat()}_to_{end.isoformat()}.pdf"
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response






from datetime import timedelta
import csv
import io
import json

from django.shortcuts import render
from django.http import HttpResponse
from django.utils import timezone
from django.db.models import Sum
from django.views.decorators.http import require_GET
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.contrib.auth import logout as django_logout
from django.views.decorators.http import require_POST
from django.db import transaction, IntegrityError
from django.contrib.auth.models import User
from .forms import BusinessRegistrationForm

from .models import Business
from .forms import BusinessRegistrationForm, EmailLoginForm
from datetime import date, datetime, timedelta
from xhtml2pdf import pisa
import io
from sales.models import Invoice
from purchases.models import Purchase
from expenses.models import Expense



AMOUNT_FIELD_CANDIDATES = [
    'amount', 'total', 'amount_paid', 'grand_total', 'net_total',
    'subtotal_taxable', 'subtotal_exempt', 'tax_total', 'cgst_total', 'sgst_total'
]
DATE_FIELD_CANDIDATES = ['date', 'created_at', 'created', 'issue_date']


def _get_model_field_set(model):
    return {f.name for f in model._meta.get_fields()}


def _detect_amount_field(queryset):
    """Return the most likely numeric amount field name for a model queryset."""
    model = queryset.model
    fields = _get_model_field_set(model)
    for fname in AMOUNT_FIELD_CANDIDATES:
        if fname in fields:
            return fname
    return None


def _detect_date_field(queryset):
    """Return the most likely date field name for a model queryset."""
    model = queryset.model
    fields = _get_model_field_set(model)
    for dname in DATE_FIELD_CANDIDATES:
        if dname in fields:
            return dname
    return None


def _start_of_day(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_week(dt):
    start = dt - timedelta(days=dt.weekday())
    return _start_of_day(start)


def _start_of_month(dt):
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _coerce_number(x):
    """
    Convert Decimal/None/etc to plain int or float suitable for JSON/JS.
    """
    if x is None:
        return 0
    try:
        f = float(x)
        # If it's effectively an integer, return int
        if abs(f - int(f)) < 1e-9:
            return int(f)
        return f
    except Exception:
        try:
            return int(x)
        except Exception:
            return 0


def _date_range_totals(queryset, start, end):
    """
    Aggregate the best numeric field for queryset between start (inclusive) and end (exclusive).
    Returns 0 if field not found or no rows.
    """
    field_name = _detect_amount_field(queryset)
    date_field = _detect_date_field(queryset)
    if not field_name or not date_field:
        return 0
    gte = {f"{date_field}__gte": start}
    lt = {f"{date_field}__lt": end}
    qs = queryset.filter(**gte).filter(**lt)
    agg = qs.aggregate(total=Sum(field_name))
    return agg['total'] or 0


def _range_per_day(queryset, start_date, days_count):
    """
    Returns (labels, values) with values coerced to plain numbers.
    """
    labels = []
    values = []
    for i in range(days_count):
        day_start = start_date + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        labels.append(day_start.strftime('%b %d'))
        raw = _date_range_totals(queryset, day_start, day_end)
        values.append(_coerce_number(raw))
    return labels, values


@require_GET
def admin_dashboard(request):
    now = timezone.localtime()
    today_start = _start_of_day(now)
    tomorrow = today_start + timedelta(days=1)
    week_start = _start_of_week(now)
    next_week = week_start + timedelta(days=7)
    month_start = _start_of_month(now)
    # next_month: first day of next month
    next_month = (month_start + timedelta(days=32)).replace(day=1)

    # Querysets (replace with your concrete query if needed)
    sales_qs = Invoice.objects.all()
    purchases_qs = Purchase.objects.all()
    expenses_qs = Expense.objects.all()

    # Totals for different periods (coerced to numeric types)
    totals = {
        'today': {
            'sales': _coerce_number(_date_range_totals(sales_qs, today_start, tomorrow)),
            'purchases': _coerce_number(_date_range_totals(purchases_qs, today_start, tomorrow)),
            'expenses': _coerce_number(_date_range_totals(expenses_qs, today_start, tomorrow)),
        },
        'week': {
            'sales': _coerce_number(_date_range_totals(sales_qs, week_start, next_week)),
            'purchases': _coerce_number(_date_range_totals(purchases_qs, week_start, next_week)),
            'expenses': _coerce_number(_date_range_totals(expenses_qs, week_start, next_week)),
        },
        'month': {
            'sales': _coerce_number(_date_range_totals(sales_qs, month_start, next_month)),
            'purchases': _coerce_number(_date_range_totals(purchases_qs, month_start, next_month)),
            'expenses': _coerce_number(_date_range_totals(expenses_qs, month_start, next_month)),
        }
    }

    def profit_calc(period):
        s = totals[period]['sales'] or 0
        p = totals[period]['purchases'] or 0
        e = totals[period]['expenses'] or 0
        return _coerce_number(s - (p + e))

    profits = {
        'today': profit_calc('today'),
        'week': profit_calc('week'),
        'month': profit_calc('month'),
    }

    # Businesses & per-business stats for display
    businesses = Business.objects.all().order_by('name')
    business_stats = []
    for b in businesses:
        b_sales = Invoice.objects.filter(business=b)
        b_purchases = Purchase.objects.filter(business=b)
        b_expenses = Expense.objects.filter(business=b)
        ms = _coerce_number(_date_range_totals(b_sales, month_start, next_month))
        mp = _coerce_number(_date_range_totals(b_purchases, month_start, next_month))
        me = _coerce_number(_date_range_totals(b_expenses, month_start, next_month))
        business_stats.append({
            'business': b,
            'today_sales': _coerce_number(_date_range_totals(b_sales, today_start, tomorrow)),
            'month_sales': ms,
            'month_purchases': mp,
            'month_expenses': me,
            'month_profit': _coerce_number(ms - (mp + me)),
        })

    # Last 7 days (labels + values)
    last7_start = today_start - timedelta(days=6)
    labels_7, sales_7 = _range_per_day(sales_qs, last7_start, 7)
    _, purchases_7 = _range_per_day(purchases_qs, last7_start, 7)
    _, expenses_7 = _range_per_day(expenses_qs, last7_start, 7)

    days_week = []
    for i, lbl in enumerate(labels_7):
        s = sales_7[i] if i < len(sales_7) else 0
        p = purchases_7[i] if i < len(purchases_7) else 0
        e = expenses_7[i] if i < len(expenses_7) else 0
        days_week.append({'label': lbl, 'sales': s, 'purchases': p, 'expenses': e, 'profit': _coerce_number(s - (p + e))})

    # Month daywise up to today
    days_in_month_so_far = (now.date() - month_start.date()).days + 1
    labels_month, sales_month = _range_per_day(sales_qs, month_start, days_in_month_so_far)
    _, purchases_month = _range_per_day(purchases_qs, month_start, days_in_month_so_far)
    _, expenses_month = _range_per_day(expenses_qs, month_start, days_in_month_so_far)

    days_month = []
    for i, lbl in enumerate(labels_month):
        s = sales_month[i] if i < len(sales_month) else 0
        p = purchases_month[i] if i < len(purchases_month) else 0
        e = expenses_month[i] if i < len(expenses_month) else 0
        days_month.append({'label': lbl, 'sales': s, 'purchases': p, 'expenses': e, 'profit': _coerce_number(s - (p + e))})

    # Build chart dictionary (numbers already plain ints/floats)
    chart = {
        'labels': labels_7,
        'sales': sales_7,
        'purchases': purchases_7,
        'expenses': expenses_7,
        'days': days_week,
        'month': {
            'labels': labels_month,
            'sales': sales_month,
            'purchases': purchases_month,
            'expenses': expenses_month,
            'days': days_month,
        }
    }

    # JSON strings to embed safely in template (we'll escape them there)
    chart_json = json.dumps(chart)
    totals_json = json.dumps(totals)
    profits_json = json.dumps(profits)

    context = {
        'businesses': businesses,
        'business_stats': business_stats,
        'totals': totals,
        'profits': profits,
        'chart': chart,
        'chart_json': chart_json,
        'totals_json': totals_json,
        'profits_json': profits_json,
        'now': now,
        'total': businesses.count(),
        'pending_count': businesses.filter(status='pending').count() if hasattr(Business, 'status') else 0,
        'approved_count': businesses.filter(is_approved=True).count() if hasattr(Business, 'is_approved') else 0,
    }

    return render(request, 'accounts/admin/admin_dashboard.html', context)


# ----------------- CSV / PDF exports -----------------
@require_GET
def export_csv(request):
    now = timezone.localtime()
    today = _start_of_day(now)
    tomorrow = today + timedelta(days=1)

    sales_total = _coerce_number(_date_range_totals(Invoice.objects.all(), today, tomorrow))
    purchases_total = _coerce_number(_date_range_totals(Purchase.objects.all(), today, tomorrow))
    expenses_total = _coerce_number(_date_range_totals(Expense.objects.all(), today, tomorrow))
    profit = _coerce_number(sales_total - (purchases_total + expenses_total))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Metric', 'Value'])
    writer.writerow(['Sales (today)', sales_total])
    writer.writerow(['Purchases (today)', purchases_total])
    writer.writerow(['Expenses (today)', expenses_total])
    writer.writerow(['Profit (today)', profit])

    response = HttpResponse(buf.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="dashboard-today-summary.csv"'
    return response


# Optional PDF export (requires reportlab)
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False


@require_GET
def export_pdf(request):
    start, end = _parse_period(request)

    sales_total = _coerce_number(_date_range_totals(Invoice.objects.all(), start, end))
    purchases_total = _coerce_number(_date_range_totals(Purchase.objects.all(), start, end))
    expenses_total = _coerce_number(_date_range_totals(Expense.objects.all(), start, end))
    profit = _coerce_number(sales_total - (purchases_total + expenses_total))

    # admin name
    admin_name = ''
    if request.user and request.user.is_authenticated:
        if hasattr(request.user, 'get_full_name'):
            admin_name = request.user.get_full_name() or request.user.username
        else:
            admin_name = getattr(request.user, 'username', '')

    # logo
    logo_url = None
    try:
        from django.templatetags.static import static
        logo_url = request.build_absolute_uri(static('img/logo.png'))
    except Exception:
        logo_url = None

    context = {
        'admin_name': admin_name,
        'logo_url': logo_url,
        'start': start,
        'end': end,
        'generated_at': datetime.now().strftime("%b %d, %Y, %I:%M %p"),
        'sales_total': f"{sales_total:.2f}",
        'purchases_total': f"{purchases_total:.2f}",
        'expenses_total': f"{expenses_total:.2f}",
        'profit': f"{profit:.2f}",
    }

    html = render_to_string('pdf/admin_dashboard.html', context=context, request=request)

    result = io.BytesIO()
    pisa_status = pisa.CreatePDF(src=html, dest=result)
    if pisa_status.err:
        return HttpResponse(html, content_type='text/html')

    pdf = result.getvalue()
    result.close()

    filename = f"admin_dashboard_{start.date()}_to_{(end - timedelta(days=1)).date()}.pdf"
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
