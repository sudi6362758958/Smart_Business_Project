# Market/products/views.py
from decimal import Decimal

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.auth.decorators import login_required

from accounts.models import Business
from .models import Product
from .forms import ProductForm
from django.db.models.deletion import ProtectedError
from django.db import transaction

from purchases.models import PurchaseItem
from sales.models import InvoiceItem
from django.contrib import messages
from django.http import Http404



# Product detail page (optional — you said you don't use it, safe to keep)
@ensure_csrf_cookie  # ensures CSRF cookie is set for JS to read
@login_required
def product_detail(request, pk):
    business = request.user.businesses.first()
    product = get_object_or_404(Product, pk=pk, business=business)
    # Fetch purchase history
    purchase_items = PurchaseItem.objects.filter(product=product).select_related("purchase").order_by("-purchase__date")
    # Fetch sales history
    sales_items = InvoiceItem.objects.filter(product=product).select_related("invoice").order_by("-invoice__date")

    context = {
        "product": product,
        "purchase_items": purchase_items,
        "sales_items": sales_items,
    }
    return render(request, "products/product_detail.html", context)

@login_required
def product_edit(request, pk):
    business = request.user.businesses.first()
    product = get_object_or_404(Product, pk=pk, business=business)

    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            return redirect("products:product_detail", pk=pk)
    else:
        form = ProductForm(instance=product)

    return render(request, "products/product_form.html", {"form": form, "product": product})



# Product list (you already have product_list.html)
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.shortcuts import get_object_or_404, render
from accounts.models import Business  # adjust import if different

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.shortcuts import get_object_or_404, render
from .models import Product
from accounts.models import Business  # adjust import path if different


@login_required
def product_list(request):
    # Get the business for the logged-in user
    business = get_object_or_404(Business, owner=request.user)

    qs = Product.objects.filter(business=business).select_related('business').order_by('name')

    # --- Filters (GET) ---
    q = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    if q:
        qs = qs.filter(name__icontains=q)

    if category:
        qs = qs.filter(category__iexact=category)

    # distinct categories to populate select box (exclude empty / null)
    categories = (
        Product.objects.filter(business=business)
        .exclude(category__isnull=True)
        .exclude(category__exact='')
        .values_list('category', flat=True)
        .distinct()
        .order_by('category')
    )

    # --- Pagination / rows per page ---
    try:
        per_page = int(request.GET.get('per_page', 25))
    except (TypeError, ValueError):
        per_page = 25
    if per_page not in (10, 25, 50, 100):
        per_page = 25

    paginator = Paginator(qs, per_page)
    page_num = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # build querystring without the 'page' param so pagination links don't duplicate page keys
    qs_copy = request.GET.copy()
    if 'page' in qs_copy:
        qs_copy.pop('page')
    querystring = qs_copy.urlencode()  # can be empty string

    context = {
        'business': business,
        'products': page_obj.object_list,   # items for current page
        'page_obj': page_obj,
        'paginator': paginator,
        'q': q,
        'category': category,
        'categories': categories,
        'per_page': per_page,
        'querystring': querystring,
    }
    return render(request, 'products/product_list.html', context)



# Create product view    
@login_required
def product_create(request):
    # get business for the logged-in user
    business = get_object_or_404(Business, owner=request.user)

    if request.method == "POST":
        # copy POST so we can safely override the 'business' value server-side
        post = request.POST.copy()

        # Force the business value to the correct id (server-side authority)
        post['business'] = str(business.pk)

        # instantiate form with the cleaned POST and pass business for queryset limiting if your form needs it
        form = ProductForm(post, business=business)

        if form.is_valid():
            product = form.save(commit=False)
            # ensure the instance has the correct business (double safety)
            product.business = business
            product.save()
            return redirect("products:product_list")
        else:
            # debug: show form errors in console (remove later)
            print("ProductForm errors:", form.errors)
    else:
        form = ProductForm(business=business)

    return render(request, "products/product_form.html", {
        "form": form,
        "business": business,
    })
    
@login_required
def product_delete(request, pk):
    """
    Delete a product.

    - If product not found -> friendly message.
    - If ProtectedError and user is owner -> FORCE DELETE (delete related purchase/invoice items).
    - If ProtectedError and user is NOT owner -> show 'cannot delete' page.
    """
    # Safe product fetch:
    try:
        product = get_object_or_404(Product, pk=pk)
    except Http404:
        messages.error(request, "Product not found or already deleted.")
        return redirect("products:product_list")

    # Determine if request.user is the owner (or superuser)
    is_owner = (
        request.user.is_superuser or
        (product.business and product.business.owner_id == request.user.id)
    )

    if request.method == "POST":
        try:
            product.delete()
            messages.success(request, f"Product '{product.name}' deleted successfully.")
            return redirect("products:product_list")
        except ProtectedError:
            if is_owner:
                # OWNER FORCE DELETE
                with transaction.atomic():
                    PurchaseItem.objects.select_for_update().filter(product=product).delete()
                    InvoiceItem.objects.select_for_update().filter(product=product).delete()
                    product.delete()

                messages.success(
                    request,
                    f"Product '{product.name}' and its related items were deleted by the owner."
                )
                return redirect("products:product_list")

            else:
                # Non-owner → show error page
                purchases = PurchaseItem.objects.filter(product=product)
                invoices = InvoiceItem.objects.filter(product=product)
                messages.error(
                    request,
                    "Cannot delete product because it is referenced by purchases or invoices."
                )
                return render(
                    request,
                    "products/cannot_delete.html",
                    {"product": product, "related": {"purchases": purchases, "invoices": invoices}}
                )

    # GET — show confirmation
    return render(request, "products/confirm_delete.html", {"product": product})

# products/views.py (add these imports at top if not present)
import csv
from datetime import datetime
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404


# ... your other views like product_list above ...

@login_required
def export_selected(request):
    """
    Exports selected product rows as CSV.
    Expects GET params:
      - ids: comma separated product ids (e.g. ids=1,2,3)
      - business: business pk (ensures user owns the business)
    """
    ids = request.GET.get('ids', '')
    business_pk = request.GET.get('business')

    if not ids:
        return HttpResponseBadRequest("No ids provided.")

    if not business_pk:
        return HttpResponseBadRequest("Missing business parameter.")

    # confirm business belongs to request.user
    business = get_object_or_404(Business, pk=business_pk)
    if business.owner != request.user:
        return HttpResponseForbidden("You don't have permission to export this business data.")

    # parse ids safely
    try:
        id_list = [int(x) for x in ids.split(',') if x.strip()]
    except ValueError:
        return HttpResponseBadRequest("Invalid ids parameter.")

    qs = Product.objects.filter(pk__in=id_list, business=business)

    # Prepare CSV response
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"products_business{business_pk}_{timestamp}.csv"
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    # header row - adapt columns as needed
    writer.writerow([
        'id', 'name', 'category', 'base_unit', 'price_per_unit',
        'remaining_stock', 'is_low_stock'
    ])

    for p in qs:
        # prefer display property if available
        base_unit = p.get_base_unit_display() if hasattr(p, 'get_base_unit_display') else getattr(p, 'base_unit', '')
        remaining = getattr(p, 'remaining_stock_display', getattr(p, 'remaining_stock', ''))
        writer.writerow([
            p.pk,
            p.name,
            p.category or '',
            base_unit,
            str(p.price_per_unit),
            remaining,
            'Yes' if getattr(p, 'is_low_stock', False) else 'No'
        ])

    return response
