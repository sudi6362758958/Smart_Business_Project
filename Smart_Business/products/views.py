# Market/products/views.py
from decimal import Decimal

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.auth.decorators import login_required

from accounts.models import Business
from .models import Product
from .forms import PriceCalculatorForm, ProductForm
from django.db.models.deletion import ProtectedError
from django.db import transaction

from .models import Product
from purchases.models import PurchaseItem
from sales.models import InvoiceItem
from django.contrib import messages
from django.http import Http404



# Product detail page (optional — you said you don't use it, safe to keep)
@ensure_csrf_cookie  # ensures CSRF cookie is set for JS to read
def product_detail(request, pk):
    """
    Renders a product detail page with the price calculator.
    (Optional — keep or remove if you don't have product_detail.html)
    """
    product = get_object_or_404(Product, pk=pk)
    calc_form = PriceCalculatorForm(product=product, initial={
        "quantity": Decimal("1.000"),
        "unit": product.base_unit
    })
    context = {
        "product": product,
        "form": calc_form,
    }
    return render(request, "products/product_detail.html", context)


@require_POST
def price_calculator_api(request):
    """
    AJAX endpoint: expects POST with 'product_id', 'quantity', 'unit'.
    Returns JSON: {"ok": True, "total": "49.00"} or {"ok": False, "errors": {...}}
    """
    product_id = request.POST.get("product_id") or request.POST.get("product")
    quantity = request.POST.get("quantity")
    unit = request.POST.get("unit")

    if not product_id:
        return JsonResponse({"ok": False, "errors": {"product_id": ["Missing product_id"]}}, status=400)

    product = get_object_or_404(Product, pk=product_id)

    form = PriceCalculatorForm(data={"quantity": quantity, "unit": unit}, product=product)

    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": form.errors}, status=400)

    total = form.get_total()  # Decimal
    return JsonResponse({"ok": True, "total": f"{total:.2f}"})


# Product list (you already have product_list.html)
@login_required
def product_list(request):
    # Get the business for the logged-in user
    business = get_object_or_404(Business, owner=request.user)

    # Query products for this business.
    # Do NOT use select_related('category') because category is now a CharField (non-relational).
    products = Product.objects.filter(business=business).select_related('business').order_by('name')

    return render(request, 'products/product_list.html', {
        'business': business,
        'products': products,
    })

# Create product view
@login_required
@login_required
    
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