# Market/sales/forms.py
from decimal import Decimal
from typing import Optional

from django import forms
from django.forms import inlineformset_factory

from .models import Customer, Invoice, InvoiceItem
from products.models import Product


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "email", "phone", "address"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Customer name"}
            ),
            "email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "Email (optional)"}
            ),
            "phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Phone (optional)"}
            ),
            "address": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 2,
                    "placeholder": "Address (optional)",
                }
            ),
        }


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = [
            "business",
            "customer",
            "invoice_no",
            "date",
            "amount_paid",
            "status",
            "notes",
        ]
        widgets = {
            "business": forms.HiddenInput(),
            "customer": forms.Select(attrs={"class": "form-select"}),
            "invoice_no": forms.TextInput(
                attrs={"class": "form-control", "readonly": "readonly"}
            ),
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "amount_paid": forms.NumberInput(
                attrs={"step": "0.1", "class": "form-control"}
            ),
            "status": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, business: Optional[object] = None, **kwargs):
        """
        - Accepts `business` kwarg from the view.
        - Limits customer queryset to that business.
        - NOTE: we DO NOT call business.generate_invoice_no() here to avoid
          incrementing the business sequence during a simple GET/preview.
          The invoice number is generated and saved atomically in the view on POST.
        """
        super().__init__(*args, **kwargs)

        # invoice_no is auto-generated -> not required from user
        self.fields["invoice_no"].required = False

        # Set business initial value + customer queryset
        if business:
            # set hidden business field initial to the business PK so it round-trips on POST
            try:
                self.fields["business"].initial = business.pk
            except Exception:
                # fallback: if business is already a pk or something else
                self.fields["business"].initial = business

            # limit customers to business
            self.fields["customer"].queryset = Customer.objects.filter(
                business=business
            ).order_by("name")
        else:
            self.fields["customer"].queryset = Customer.objects.none()

        # IMPORTANT: do NOT pre-generate invoice_no here using business.generate_invoice_no()
        # because that method increments the DB counter. We will generate and persist a
        # unique invoice_no in the view when saving the form (server-side, atomic).

    def clean_business(self):
        business = self.cleaned_data.get("business")
        if not business:
            raise forms.ValidationError("Business is required")
        return business


class InvoiceItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = [
            "product",
            "uom",  # includes UOM field
            "quantity",
            "unit_price",
            "tax_percent",
        ]
        widgets = {
            "product": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "uom": forms.NumberInput(
                attrs={
                    "step": "0.050",
                    "min": "0",
                    "class": "form-control form-control-sm",
                }
            ),
            "quantity": forms.NumberInput(
                attrs={
                    "step": "1",
                    "min": "0",
                    "class": "form-control form-control-sm",
                }
            ),
            "unit_price": forms.NumberInput(
                attrs={
                    "step": "1",
                    "min": "0",
                    "class": "form-control form-control-sm",
                }
            ),
            "tax_percent": forms.NumberInput(
                attrs={
                    "step": "1",
                    "min": "0",
                    "max": "100",
                    "class": "form-control form-control-sm",
                }
            ),
        }

    def __init__(self, *args, business: Optional[object] = None, **kwargs):
        """
        Accepts optional `business` kwarg. When provided, we filter the product queryset
        to show only products belonging to that business.
        """
        super().__init__(*args, **kwargs)

        # Default: no products
        try:
            if business:
                self.fields["product"].queryset = Product.objects.filter(
                    business=business
                ).order_by("name")
            else:
                self.fields["product"].queryset = Product.objects.none()
        except Exception:
            # If product field or model differs, fall back to the default queryset
            pass

        # sensible default
        if "quantity" in self.fields and not self.fields["quantity"].initial:
            self.fields["quantity"].initial = 1

    def clean_quantity(self) -> Decimal:
        q = self.cleaned_data.get("quantity")
        if q is None or q <= 0:
            raise forms.ValidationError("Quantity must be greater than 0")
        return Decimal(str(q))

    def clean_unit_price(self) -> Decimal:
        p = self.cleaned_data.get("unit_price")
        if p is None:
            return Decimal("0")
        return Decimal(str(p))

    def clean_tax_percent(self) -> Decimal:
        t = self.cleaned_data.get("tax_percent")
        if t is None:
            return Decimal("0")
        t_decimal = Decimal(str(t))
        if t_decimal < 0 or t_decimal > 100:
            raise forms.ValidationError("Tax percent must be between 0 and 100")
        return t_decimal


InvoiceItemFormSet = inlineformset_factory(
    Invoice,
    InvoiceItem,
    form=InvoiceItemForm,
    extra=1,
    can_delete=True,
)
