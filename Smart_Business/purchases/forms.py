# Market/purchases/forms.py

from decimal import Decimal
from typing import Optional

from django import forms
from django.forms import inlineformset_factory

from .models import Purchase, PurchaseItem
from products.models import Product
from accounts.models import Business


class PurchaseForm(forms.ModelForm):
    """
    Purchase header form.
    Accepts optional `business` kwarg so views can pass the current business.
    """

    class Meta:
        model = Purchase
        fields = ["business", "supplier", "phone", "company", "date", "total"]
        widgets = {
            "business": forms.HiddenInput(),
            "supplier": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Supplier name"}
            ),
            "company": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "company name"}
            ),
            "phone": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "phone number",
                }
            ),
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "total": forms.NumberInput(
                attrs={"step": "0.01", "class": "form-control", "readonly": "readonly"}
            ),
        }

    def __init__(self, *args, business: Optional[object] = None, **kwargs):
        """
        - Accepts `business` from the view.
        - Sets it as initial on the hidden business field (use PK so it round-trips).
        """
        super().__init__(*args, **kwargs)

        # total is calculated from items, not user-entered
        self.fields["total"].required = False

        if business is not None:
            # Pre-fill hidden business field with PK (not the model instance)
            try:
                self.fields["business"].initial = business.pk
            except Exception:
                # fallback: if caller passed a pk already
                self.fields["business"].initial = business
        else:
            # If form is bound to an existing instance that has business, use it
            try:
                if getattr(self.instance, "pk", None) and getattr(self.instance, "business", None):
                    self.fields["business"].initial = self.instance.business.pk
            except Exception:
                pass

    def clean_total(self):
        """
        Total is computed in view/model from items.
        Ignore whatever might come from the browser.
        """
        return self.instance.total if self.instance.pk else Decimal("0.00")


class PurchaseItemForm(forms.ModelForm):
    """
    Line item form for a Purchase.
    Accepts optional `business` kwarg to limit product choices to that business.
    """

    class Meta:
        model = PurchaseItem
        fields = ["product", "quantity", "unit_cost"]
        widgets = {
            "product": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "quantity": forms.NumberInput(
                attrs={
                    "step": "1",
                    "min": "0",
                    "class": "form-control form-control-sm",
                }
            ),
            "unit_cost": forms.NumberInput(
                attrs={
                    "step": "0.01",
                    "min": "0",
                    "class": "form-control form-control-sm",
                }
            ),
        }

    def __init__(self, *args, business: Optional[object] = None, **kwargs):
        """
        Robustly determine business in this order:
         1) business argument passed via form_kwargs from the view (preferred)
         2) if editing an existing PurchaseItem -> infer from self.instance.purchase.business
         3) try to read initial['business'] (used by formset.empty_form or template initial)
         4) try to read business from POST data (self.data) if available

        IMPORTANT: Do NOT set defaults on the empty_form (prefix contains '__prefix__'),
        otherwise the empty form becomes non-empty and Django will validate it and fail.
        """
        super().__init__(*args, **kwargs)

        resolved_business = None

        # 1) explicit business passed in
        if business is not None:
            resolved_business = business

        # 2) infer from parent purchase if available (editing case)
        if resolved_business is None:
            try:
                parent = getattr(self.instance, "purchase", None)
                if parent is not None and getattr(parent, "business", None):
                    resolved_business = parent.business
            except Exception:
                resolved_business = None

        # 3) try initial (useful for formset.empty_form where initial may carry the business pk)
        if resolved_business is None:
            try:
                initial_biz = self.initial.get("business")
                if initial_biz:
                    # if initial_biz is a pk, fetch Business
                    if isinstance(initial_biz, Business):
                        resolved_business = initial_biz
                    else:
                        try:
                            resolved_business = Business.objects.get(pk=initial_biz)
                        except Exception:
                            resolved_business = None
            except Exception:
                resolved_business = None

        # 4) try POST/bound data (name may be 'business' or 'purchase-business' depending on nesting)
        if resolved_business is None and getattr(self, "data", None):
            try:
                data_biz = (
                    self.data.get("business")
                    or self.data.get("purchase-business")
                    or self.data.get("purchase_business")
                )
                if data_biz:
                    try:
                        resolved_business = Business.objects.get(pk=data_biz)
                    except Exception:
                        resolved_business = None
            except Exception:
                resolved_business = None

        # Apply queryset filtering based on resolved_business (or none)
        if resolved_business is not None:
            try:
                self.fields["product"].queryset = Product.objects.filter(
                    business=resolved_business
                ).order_by("name")
            except Exception:
                # If product model differs, fallback to none
                self.fields["product"].queryset = Product.objects.none()
        else:
            # safe fallback: no products when business not resolved
            self.fields["product"].queryset = Product.objects.none()

        # IMPORTANT: Do NOT pre-fill values on the formset.empty_form.
        # Detect empty_form via prefix containing '__prefix__' and skip setting initial values.
        try:
            is_empty_template = isinstance(self.prefix, str) and "__prefix__" in self.prefix
        except Exception:
            is_empty_template = False

        if not is_empty_template:
            # For real forms (existing rows) we DO NOT force defaults either — keep as-is.
            # If you want a default quantity when adding a new row, the JS will set it when user clicks Add.
            pass

    def clean_quantity(self) -> Decimal:
        q = self.cleaned_data.get("quantity")
        if q is None or q <= 0:
            raise forms.ValidationError("Quantity must be greater than 0.")
        return Decimal(str(q))

    def clean_unit_cost(self) -> Decimal:
        c = self.cleaned_data.get("unit_cost")
        if c is None or c < 0:
            raise forms.ValidationError("Unit cost must be non-negative.")
        return Decimal(str(c))


# Inline formset for purchase items
PurchaseItemFormSet = inlineformset_factory(
    Purchase,
    PurchaseItem,
    form=PurchaseItemForm,
    extra=1,  # keep empty_form available for JS
    can_delete=True,
)
