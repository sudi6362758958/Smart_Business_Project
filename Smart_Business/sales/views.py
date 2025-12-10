# Market/sales/views.py
import json
import re
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, Http404, JsonResponse
from django.utils import timezone

from accounts.models import Business
from products.models import Product

from .models import Customer, Invoice, InvoiceItem, Payment
from .forms import CustomerForm, InvoiceForm, InvoiceItemFormSet
from django.db.models import Sum
from xhtml2pdf import pisa
from io import BytesIO

from django.template.loader import get_template


@login_required
def invoice_list(request):
    business = get_object_or_404(Business, owner=request.user)
    invoices = Invoice.objects.filter(business=business).order_by("-date")
    return render(
        request,
        "sales/invoice_list.html",
        {
            "business": business,
            "invoices": invoices,
        },
    )


@login_required
def invoice_detail(request, pk):
    business = get_object_or_404(Business, owner=request.user)
    invoice = get_object_or_404(Invoice, pk=pk, business=business)
    invoice.recalc_totals()
    payments = invoice.payments.order_by("-payment_date")
    return render(
        request,
        "sales/invoice_detail.html",
        {
            "business": business,
            "invoice": invoice,
            "payments": payments,
        },
    )


@login_required
def invoice_create(request, pk=None):
    """
    Create or edit invoice (combined). Validates that requested item quantities
    do not exceed available product stock before saving. If quantity > stock,
    the corresponding form field will receive an error and the invoice will not save.
    
    When editing an invoice, stock is adjusted based on the difference between
    old and new quantities.
    """
    business = get_object_or_404(Business, owner=request.user)
    editing = False
    invoice = None
    old_items_dict = {}  # Store old invoice items for edit comparison

    if pk:
        invoice = get_object_or_404(Invoice, pk=pk, business=business)
        editing = True
        # Use InvoiceItem queryset directly so we don't rely on a reverse-related name
        old_items = InvoiceItem.objects.filter(invoice=invoice)
        old_items_dict = {item.id: item for item in old_items}

    if request.method == "POST":
        # pass business into InvoiceForm (so it can set initial invoice_no)
        form = InvoiceForm(request.POST, instance=invoice, business=business)
        # pass business into each InvoiceItemForm via form_kwargs so product queryset is filtered
        formset = InvoiceItemFormSet(
            request.POST, instance=invoice, form_kwargs={"business": business}
        )

        # first validate form + formset as usual
        if form.is_valid() and formset.is_valid():
            # Additional stock validation: ensure each non-deleted form's quantity <= product.stock_qty
            stock_ok = True
            for item_form in formset.forms:
                # Skip forms that are marked for deletion
                if getattr(item_form, "cleaned_data", None) is None:
                    continue
                if item_form.cleaned_data.get("DELETE"):
                    continue

                prod = item_form.cleaned_data.get("product")
                qty = item_form.cleaned_data.get("quantity") or Decimal("0")
                # ensure qty is Decimal
                if not isinstance(qty, Decimal):
                    try:
                        qty = Decimal(str(qty))
                    except Exception:
                        qty = Decimal("0")

                # Defensive: if product not found (shouldn't happen because formset filters products),
                # skip here (or mark error).
                if prod is None:
                    item_form.add_error(None, "Product is required.")
                    stock_ok = False
                    continue

                # Get available stock from the correct field
                available = getattr(prod, "stock_qty", None) or Decimal("0")
                if not isinstance(available, Decimal):
                    try:
                        available = Decimal(str(available))
                    except Exception:
                        available = Decimal("0")

                # If editing and this is an existing item, adjust available stock by old quantity
                if editing and item_form.instance and item_form.instance.pk:
                    old_item = old_items_dict.get(item_form.instance.pk)
                    if old_item and old_item.product == prod:
                        # Same product - adjust available stock by adding back old quantity
                        old_qty = old_item.quantity or Decimal("0")
                        if not isinstance(old_qty, Decimal):
                            try:
                                old_qty = Decimal(str(old_qty))
                            except Exception:
                                old_qty = Decimal("0")
                        available += old_qty

                # Compare: if requested quantity greater than adjusted available stock -> error
                if qty > available:
                    # attach error to quantity field so user sees it on the row
                    item_form.add_error(
                        "quantity",
                        f"The quantity for '{prod.name}' is {available} {getattr(prod, 'base_unit', '')}. "
                        "Please purchase stock before invoicing."
                    )
                    stock_ok = False

            if not stock_ok:
                # Render page with errors (form and formset already carry errors)
                products_qs = Product.objects.filter(business=business)
                products_for_js = json.dumps(
                    [
                        {
                            "id": p.id,
                            "name": p.name,
                            "price_per_unit": float(getattr(p, "price_per_unit", 0) or 0),
                        }
                        for p in products_qs
                    ]
                )

                selected_customer = None
                if invoice and getattr(invoice, "customer", None):
                    selected_customer = invoice.customer
                elif form.initial.get("customer"):
                    try:
                        selected_customer = Customer.objects.get(
                            pk=form.initial["customer"], business=business
                        )
                    except Exception:
                        selected_customer = None

                context = {
                    "form": form,
                    "formset": formset,
                    "editing": editing,
                    "business": business,
                    "products_for_js": products_for_js,
                    "customer": selected_customer,
                }
                return render(request, "sales/invoice_form.html", context)

            # If all stock ok -> proceed to save inside a transaction
            try:
                with transaction.atomic():
                    invoice = form.save(commit=False)
                    invoice.business = business  # Ensure business is set

                    # If invoice_no empty still, try to set it now (defense-in-depth)
                    if not getattr(invoice, "invoice_no", None):
                        assigned_no = None

                        # 1) Prefer business.generate_invoice_no() if available.
                        if hasattr(business, "generate_invoice_no") and callable(
                            business.generate_invoice_no
                        ):
                            attempts = 0
                            max_attempts = 5
                            while attempts < max_attempts:
                                try:
                                    candidate = business.generate_invoice_no()
                                except Exception:
                                    candidate = None
                                if not candidate:
                                    attempts += 1
                                    continue
                                # uniqueness check for this business
                                if not Invoice.objects.filter(
                                    business=business, invoice_no=candidate
                                ).exists():
                                    assigned_no = candidate
                                    break
                                attempts += 1

                        # 2) Fallback: try to extract numeric suffix from last invoice_no and increment.
                        if not assigned_no:
                            last = (
                                Invoice.objects.filter(business=business)
                                .order_by("-pk")
                                .first()
                            )
                            seq = None
                            if last and getattr(last, "invoice_no", None):
                                m = re.search(r"(\d+)$", str(last.invoice_no))
                                if m:
                                    try:
                                        seq = int(m.group(1)) + 1
                                    except Exception:
                                        seq = None

                            if seq is None:
                                try:
                                    seq = Invoice.objects.filter(business=business).count() + 1
                                except Exception:
                                    seq = 1

                            now = timezone.localtime(timezone.now())
                            date_str = now.strftime("%d-%m-%Y")
                            initials = "XX"
                            try:
                                parts = [p for p in (business.name or "").strip().split() if p]
                                if len(parts) == 0:
                                    initials = "XX"
                                elif len(parts) == 1:
                                    initials = (parts[0][:2]).upper()
                                else:
                                    initials = (parts[0][0] + parts[1][0]).upper()
                            except Exception:
                                initials = "XX"

                            assigned_no = f"{date_str}-{initials}-{seq:03d}"

                            if Invoice.objects.filter(business=business, invoice_no=assigned_no).exists():
                                try:
                                    seq = Invoice.objects.filter(business=business).count() + 1
                                    assigned_no = f"{date_str}-{initials}-{seq:03d}"
                                except Exception:
                                    ts = timezone.now().strftime("%Y%m%d%H%M%S")
                                    assigned_no = f"{date_str}-{initials}-{ts}"

                        invoice.invoice_no = assigned_no

                    invoice.save()

                    # Save formset with instance set already
                    formset.instance = invoice
                    saved_items = formset.save()  # this returns saved instances

                    # Track products and their stock adjustments
                    stock_adjustments = {}
                    
                    # Calculate stock adjustments for each item
                    for inv_item in saved_items:
                        prod = getattr(inv_item, "product", None)
                        if prod is None:
                            continue
                            
                        try:
                            qty = getattr(inv_item, "quantity", 0) or 0
                            if not isinstance(qty, Decimal):
                                qty = Decimal(str(qty))
                        except Exception:
                            qty = Decimal("0")
                        
                        # For new items, reduce stock by full quantity
                        # For existing items, adjust stock by difference
                        if editing and inv_item.pk in old_items_dict:
                            old_item = old_items_dict[inv_item.pk]
                            old_qty = old_item.quantity or Decimal("0")
                            if not isinstance(old_qty, Decimal):
                                old_qty = Decimal(str(old_qty))
                            
                            # Calculate difference (could be positive or negative)
                            diff = qty - old_qty
                            
                            # If product changed, handle both old and new products
                            if old_item.product != prod:
                                # Return stock to old product
                                if old_item.product.id not in stock_adjustments:
                                    stock_adjustments[old_item.product.id] = {
                                        'product': old_item.product,
                                        'adjustment': old_qty  # Return old quantity
                                    }
                                else:
                                    stock_adjustments[old_item.product.id]['adjustment'] += old_qty
                                
                                # Reduce stock from new product
                                if prod.id not in stock_adjustments:
                                    stock_adjustments[prod.id] = {
                                        'product': prod,
                                        'adjustment': -qty  # Reduce by new quantity
                                    }
                                else:
                                    stock_adjustments[prod.id]['adjustment'] -= qty
                            else:
                                # Same product, adjust by difference
                                if prod.id not in stock_adjustments:
                                    stock_adjustments[prod.id] = {
                                        'product': prod,
                                        'adjustment': -diff  # Negative diff means reduce stock
                                    }
                                else:
                                    stock_adjustments[prod.id]['adjustment'] -= diff
                        else:
                            # New item, reduce stock by full quantity
                            if prod.id not in stock_adjustments:
                                stock_adjustments[prod.id] = {
                                    'product': prod,
                                    'adjustment': -qty  # Reduce by quantity
                                }
                            else:
                                stock_adjustments[prod.id]['adjustment'] -= qty
                    
                    # Handle deleted items - return stock to their products
                    for form in formset.forms:
                        if form.cleaned_data and form.cleaned_data.get("DELETE") and form.instance.pk:
                            old_item = form.instance
                            old_prod = getattr(old_item, "product", None)
                            if old_prod:
                                old_qty = old_item.quantity or Decimal("0")
                                if not isinstance(old_qty, Decimal):
                                    old_qty = Decimal(str(old_qty))
                                
                                if old_prod.id not in stock_adjustments:
                                    stock_adjustments[old_prod.id] = {
                                        'product': old_prod,
                                        'adjustment': old_qty  # Return stock
                                    }
                                else:
                                    stock_adjustments[old_prod.id]['adjustment'] += old_qty

                    # Apply all stock adjustments
                    for adjustment_data in stock_adjustments.values():
                        prod = adjustment_data['product']
                        adjustment = adjustment_data['adjustment']
                        
                        current_stock = getattr(prod, "stock_qty", None) or Decimal("0")
                        try:
                            if not isinstance(current_stock, Decimal):
                                current_stock = Decimal(str(current_stock))
                        except Exception:
                            current_stock = Decimal("0")
                        
                        new_stock = current_stock + adjustment  # adjustment can be positive or negative
                        if new_stock < Decimal("0"):
                            new_stock = Decimal("0")
                        prod.stock_qty = new_stock
                        prod.save()

                    # Recalculate totals using model method (if you have one)
                    try:
                        invoice.recalc_totals()
                    except Exception:
                        pass

                    # Update status based on amount_paid and total
                    amount_paid = form.cleaned_data.get("amount_paid") or 0
                    try:
                        total = getattr(invoice, "total", 0) or 0
                        if Decimal(amount_paid) >= Decimal(total) and Decimal(total) > 0:
                            invoice.status = "Paid"
                        elif Decimal(amount_paid) > 0 and Decimal(amount_paid) < Decimal(total):
                            invoice.status = "Partial"
                        else:
                            invoice.status = "Pending"
                    except Exception:
                        pass

                    invoice.save()

                return redirect("sales:invoice_list")

            except Exception as e:
                # Log/print the error for debugging (better to use logging in prod)
                print(f"Error saving invoice: {e}")

        else:
            # invalid main form or formset - will render below with errors
            print("Form errors:", form.errors)
            print("Formset errors:", formset.errors)
    else:
        # GET: pass business to form so initial invoice_no is prepared
        form = InvoiceForm(instance=invoice, business=business)
        formset = InvoiceItemFormSet(instance=invoice, form_kwargs={"business": business})

    # Prepare product list for JS (only products belonging to this business)
    products_qs = Product.objects.filter(business=business)
    products_for_js = json.dumps(
        [
            {
                "id": p.id,
                "name": p.name,
                "price_per_unit": float(getattr(p, "price_per_unit", 0) or 0),
            }
            for p in products_qs
        ]
    )

    # Pass selected customer object if available (for display)
    selected_customer = None
    if invoice and getattr(invoice, "customer", None):
        selected_customer = invoice.customer
    elif form.initial.get("customer"):
        try:
            selected_customer = Customer.objects.get(
                pk=form.initial["customer"], business=business
            )
        except Exception:
            selected_customer = None

    context = {
        "form": form,
        "formset": formset,
        "editing": editing,
        "business": business,
        "products_for_js": products_for_js,
                        "customer": selected_customer,
    }
    return render(request, "sales/invoice_form.html", context)



# invoice_edit simply reuses invoice_create logic
@login_required
def invoice_edit(request, pk):
    return invoice_create(request, pk=pk)


# Small JSON endpoint to fetch customer details by id (used by AJAX)
@login_required
def customer_detail_json(request, pk):
    try:
        business = get_object_or_404(Business, owner=request.user)
        cust = Customer.objects.get(pk=pk, business=business)
        data = {
            "id": cust.id,
            "name": cust.name,
            "phone": cust.phone or "",
            "email": cust.email or "",
            "address": cust.address or "",
        }
        return JsonResponse({"ok": True, "customer": data})
    except Customer.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Customer not found"}, status=404)


@login_required
def customer_create(request):
    business = get_object_or_404(Business, owner=request.user)
    if request.method == "POST":
        form = CustomerForm(request.POST)
        if form.is_valid():
            cust = form.save(commit=False)
            cust.business = business
            cust.save()

            # Redirect back to invoice create with the new customer pre-selected
            return redirect(f"{reverse('sales:invoice_create')}?customer={cust.id}")
    else:
        form = CustomerForm()
    return render(
        request,
        "sales/customer_form.html",
        {
            "business": business,
            "form": form,
        },
    )


@login_required
def invoice_pdf(request, pk):
    raise Http404("PDF export not implemented in this snippet.")


@login_required
def sales_list(request):
    business = get_object_or_404(Business, owner=request.user)
    invoices = Invoice.objects.filter(business=business).order_by("-date")[:10]
    total_invoices = Invoice.objects.filter(business=business).count()
    total_due = Invoice.objects.filter(business=business).aggregate(sum=Sum("total"))[  # type: ignore[index]
        "sum"
    ] or Decimal("0.00")
    context = {
        "business": business,
        "invoices": invoices,
        "total_invoices": total_invoices,
        "total_due": total_due,
    }
    return render(request, "sales/sales_list.html", context)




@login_required
def invoice_delete(request, pk):
    business = get_object_or_404(Business, owner=request.user)
    invoice = get_object_or_404(Invoice, pk=pk, business=business)

    if request.method == "POST":
        invoice.delete()
        return redirect("sales:invoice_list")

    raise Http404("Invalid request method")
