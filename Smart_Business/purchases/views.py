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


def get_business_for_user(user):
    """
    Adjust this helper if your Business -> User relation differs.
    Currently assumes Business has an 'owner' FK to auth.User.
    """
    qs = Business.objects.filter(owner=user)
    if not qs.exists():
        return None
    return qs.first()


def purchase_list(request):
    business = get_business_for_user(request.user)
    if business is None:
        raise Http404("Business not found for user")

    purchases = Purchase.objects.filter(business=business).order_by(
        "-date", "-created_at"
    )
    return render(
        request,
        "purchase/purchase_list.html",
        {"business": business, "purchases": purchases},
    )


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
