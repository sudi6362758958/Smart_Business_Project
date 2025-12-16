# market/purchase/views.py
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from accounts.models import Business
from products.models import Product
from .forms import PurchaseForm, PurchaseItemFormSet
from .models import Purchase, PurchaseItem


# purchases/views.py
from datetime import datetime
import csv
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q, Prefetch
from django.core.exceptions import FieldError
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse

from .models import Purchase, PurchaseItem, Business  # adjust as needed
# If Business is in another app, import appropriately

# Helper: get business for user (you had get_business_for_user earlier)
def get_business_for_user(user):
    # replace with your own logic if different
    return Business.objects.filter(owner=user).first()


@login_required
def purchase_list(request):
    business = get_business_for_user(request.user)
    if business is None:
        raise Http404("Business not found for user")

    # Base queryset
    qs = Purchase.objects.filter(business=business).order_by('-date', '-created_at')

    # Prefetch items->product to avoid N+1 queries
    qs = qs.prefetch_related(Prefetch('items', queryset=PurchaseItem.objects.select_related('product')))

    # ---- GET filters ----
    q = request.GET.get('q', '').strip()  # optional general search
    supplier_q = request.GET.get('supplier', '').strip()
    company_q = request.GET.get('company', '').strip()
    product_q = request.GET.get('product', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    # General q: search invoice number, supplier string, company
    if q:
        # try to search in a few fields safely
        q_filters = Q()
        q_filters |= Q(pk__icontains=q)  # unlikely, but no harm
        q_filters |= Q(company__icontains=q)  # if company is text field on Purchase
        # supplier may be FK or text; try common related fields (safe approach)
        try:
            supplier_field = Purchase._meta.get_field('supplier')
            # if it's FK, search related name fields where available
            if supplier_field.get_internal_type() in ('ForeignKey', 'ManyToOneRel'):
                q_filters |= Q(supplier__name__icontains=q)
                q_filters |= Q(supplier__full_name__icontains=q)
            else:
                q_filters |= Q(supplier__icontains=q)
        except Exception:
            # fallback - try text
            try:
                q_filters |= Q(supplier__icontains=q)
            except Exception:
                pass

        qs = qs.filter(q_filters)

    # Supplier filter (robust for FK/text)
    if supplier_q:
        try:
            field = Purchase._meta.get_field('supplier')
            if field.get_internal_type() in ('ForeignKey',):
                # try common related name fields
                applied = False
                for lookup in ('supplier__name__icontains', 'supplier__full_name__icontains',
                               'supplier__first_name__icontains', 'supplier__last_name__icontains'):
                    try:
                        qs = qs.filter(**{lookup: supplier_q})
                        applied = True
                        break
                    except FieldError:
                        continue
                if not applied:
                    # last resort: try supplier__icontains
                    try:
                        qs = qs.filter(supplier__icontains=supplier_q)
                    except Exception:
                        pass
            else:
                qs = qs.filter(supplier__icontains=supplier_q)
        except Exception:
            # fallback
            try:
                qs = qs.filter(supplier__name__icontains=supplier_q)
            except Exception:
                pass

    # Company filter (likely a text field on Purchase)
    if company_q:
        qs = qs.filter(company__icontains=company_q)

    # product filter — search purchases that include an item whose product name matches
    if product_q:
        qs = qs.filter(items__product__name__icontains=product_q).distinct()

    # Date range
    if date_from:
        try:
            df = datetime.strptime(date_from, '%Y-%m-%d').date()
            qs = qs.filter(date__gte=df)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, '%Y-%m-%d').date()
            qs = qs.filter(date__lte=dt)
        except ValueError:
            pass

    # ---- Pagination / per_page ----
    try:
        per_page = int(request.GET.get('per_page', 25))
    except (TypeError, ValueError):
        per_page = 25
    per_page = per_page if per_page in (1, 25, 50, 100) else 25

    page = request.GET.get('page', 1)
    paginator = Paginator(qs, per_page)
    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    purchases = page_obj.object_list

    context = {
        'business': business,
        'purchases': purchases,
        'page_obj': page_obj,
        'paginator': paginator,
        'request': request,
    }
    return render(request, 'purchase/purchase_list.html', context)


@login_required
def purchase_export(request):
    """
    Export selected purchases (ids via GET ?ids=1,2,3) as CSV.
    Adjust to Excel/Pandas/xlsxwriter if you prefer XLSX.
    """
    ids = request.GET.get('ids', '')
    if not ids:
        return HttpResponse("No purchase ids provided.", status=400)

    id_list = [int(x) for x in ids.split(',') if x.strip().isdigit()]
    if not id_list:
        return HttpResponse("No valid purchase ids provided.", status=400)

    business = get_business_for_user(request.user)
    if business is None:
        return HttpResponse("Business not found", status=404)

    qs = Purchase.objects.filter(pk__in=id_list, business=business).prefetch_related('items__product')

    if not qs.exists():
        return HttpResponse("No purchases found for given ids.", status=404)

    # Build CSV
    filename = "purchases_export.csv"
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)

    # header
    writer.writerow(['Purchase ID', 'Date', 'Supplier', 'Company', 'Total', 'Product', 'Quantity', 'Unit Cost'])

    for p in qs:
        items = list(getattr(p, 'items', []).all()) if hasattr(p, 'items') else []
        if not items:
            writer.writerow([p.pk, p.date, str(p.supplier), p.company, p.total, '', '', ''])
        else:
            for it in items:
                pname = getattr(getattr(it, 'product', None), 'name', '')
                qty = getattr(it, 'quantity', '')
                unit_cost = getattr(it, 'unit_cost', '')
                writer.writerow([p.pk, p.date, str(p.supplier), p.company, p.total, pname, qty, unit_cost])

    return response



def purchase_create(request, pk=None):
    """
    Create a new purchase (or edit when pk provided).
    Always pass `business` into both PurchaseForm and PurchaseItemFormSet so
    product select lists are filtered to that business.
    """
    business = get_business_for_user(request.user)
    if not business:
        raise Http404("Business not found")

    purchase = None
    if pk:  # editing existing purchase - ensure it belongs to this business
        purchase = get_object_or_404(Purchase, pk=pk, business=business)

    if request.method == "POST":
        # pass business into both form and formset on POST
        form = PurchaseForm(request.POST, instance=purchase, business=business)
        formset = PurchaseItemFormSet(
            request.POST,
            instance=purchase,
            form_kwargs={"business": business},
        )

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():

                # If editing, roll back stock first
                if purchase:
                    for item in purchase.items.all():
                        product = item.product
                        product.stock_qty = Decimal(product.stock_qty) - Decimal(
                            item.quantity
                        )
                        product.save()

                purchase = form.save(commit=False)
                purchase.business = business
                purchase.save()

                items = formset.save(commit=False)

                # Delete removed items
                for obj in formset.deleted_objects:
                    obj.delete()

                # Add/Update stock for saved items
                for item in items:
                    item.purchase = purchase
                    item.save()
                    product = item.product
                    product.stock_qty = Decimal(product.stock_qty) + Decimal(
                        item.quantity
                    )
                    product.save()

                # Calculate total if empty
                if purchase.total == 0:
                    purchase.total = sum(
                        i.quantity * i.unit_cost for i in purchase.items.all()
                    )
                    purchase.save()

            messages.success(request, "Purchase saved successfully.")
            return redirect("purchases:purchase_list")

        else:
            messages.error(request, "Please correct the errors below.")

    else:
        # GET: pass business into form and formset so product selects are filtered
        form = PurchaseForm(instance=purchase, business=business)
        formset = PurchaseItemFormSet(instance=purchase, form_kwargs={"business": business})

    return render(
        request,
        "purchase/purchase_form.html",
        {"form": form, "formset": formset, "editing": bool(pk)},
    )


def purchase_edit(request, pk):
    """
    Edit purchase: ensure purchase belongs to the user's business.
    Use formset.save() to reliably persist inline items and deletions.
    """
    business = get_business_for_user(request.user)
    if business is None:
        raise Http404("Business not found for user")

    purchase = get_object_or_404(Purchase, pk=pk, business=business)

    if request.method == "POST":
        form = PurchaseForm(request.POST, instance=purchase, business=business)
        formset = PurchaseItemFormSet(
            request.POST, instance=purchase, form_kwargs={"business": business}
        )

        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    # 1) Roll back stock for existing items (undo previous quantities)
                    for old_item in purchase.items.all():
                        prod = old_item.product
                        prod.stock_qty = Decimal(prod.stock_qty) - Decimal(old_item.quantity)
                        prod.save()

                    # 2) Save purchase header
                    purchase = form.save(commit=False)
                    purchase.business = business
                    purchase.save()

                    # 3) Attach formset to saved purchase and save all item changes (create/update/delete)
                    formset.instance = purchase
                    formset.save()  # this will handle adds/updates/deletes

                    # 4) Add stock for current items (after formset.save())
                    for new_item in purchase.items.all():
                        prod = new_item.product
                        prod.stock_qty = Decimal(prod.stock_qty) + Decimal(new_item.quantity)
                        prod.save()

                    # 5) Recalculate and persist total
                    purchase.total = sum(i.quantity * i.unit_cost for i in purchase.items.all())
                    purchase.save()

                messages.success(request, "Purchase updated successfully.")
                return redirect("purchases:purchase_list")
            except Exception as e:
                # rollback will occur automatically because of transaction.atomic()
                messages.error(request, f"Error saving purchase: {e}")
        else:
            # Show form/formset errors for debugging
            # (You can remove printing in production)
            print("PurchaseForm errors:", form.errors)
            print("PurchaseItemFormSet errors:", formset.errors)
            messages.error(request, "Please correct the errors below.")
    else:
        # GET — pass business to both form and formset
        form = PurchaseForm(instance=purchase, business=business)
        formset = PurchaseItemFormSet(instance=purchase, form_kwargs={"business": business})

    context = {
        "form": form,
        "formset": formset,
        "editing": True,
        "purchase": purchase,
    }
    return render(request, "purchase/purchase_form.html", context)


def purchase_detail(request, pk):
    business = get_business_for_user(request.user)
    if business is None:
        raise Http404("Business not found for user")

    purchase = get_object_or_404(Purchase, pk=pk, business=business)
    items = purchase.items.select_related("product").all()
    return render(
        request,
        "purchase/purchase_detail.html",
        {"business": business, "purchase": purchase, "items": items},
    )


@require_http_methods(["POST"])
def purchase_delete(request, pk):
    business = get_business_for_user(request.user)
    if business is None:
        raise Http404("Business not found for user")

    purchase = get_object_or_404(Purchase, pk=pk, business=business)
    # When deleting, reduce stock by purchase quantities
    with transaction.atomic():
        for it in purchase.items.all():
            p = it.product
            p.stock_qty = Decimal(p.stock_qty) - Decimal(it.quantity)
            p.save()
        purchase.delete()

    messages.success(request, "Purchase deleted and stock adjusted.")
    return redirect(reverse("purchases:purchase_list"))
