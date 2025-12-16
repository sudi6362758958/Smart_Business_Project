# market/products/forms.py
from decimal import Decimal, InvalidOperation
from typing import Optional

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Product, Category


class ProductForm(forms.ModelForm):
    """
    ModelForm for creating/updating products.
    Minimal changes:
      - accepts `business` kwarg in __init__ to limit category queryset
      - adds optional `new_category` text field to allow manual category entry
      - creates/gets Category on save when new_category provided
    """

    new_category = forms.CharField(
        max_length=100,
        required=False,
        label="New category",
        widget=forms.TextInput(
            attrs={"placeholder": "e.g. Dairy Products", "class": "form-control"}
        ),
    )

    class Meta:
        model = Product
        fields = [
            "business",
            "category",
            "name",
            "base_unit",
            "price_per_unit",
            "stock_qty",
            "low_stock_threshold",
        ]
        widgets = {
            "business": forms.HiddenInput(),  # typically set in view
            "category": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(
                attrs={"placeholder": "Product name", "class": "form-control"}
            ),
            "base_unit": forms.Select(attrs={"class": "form-select"}),
            "price_per_unit": forms.NumberInput(
                attrs={"step": "0.01", "class": "form-control"}
            ),
            "stock_qty": forms.NumberInput(
                attrs={"step": "1", "class": "form-control"}
            ),
            "low_stock_threshold": forms.NumberInput(
                attrs={"step": "", "class": "form-control"}
            ),
        }

    def __init__(self, *args, business: Optional[object] = None, **kwargs):
        """
        Accept an optional `business` kwarg. If provided limit category queryset to that business.
        """
        self.business = business
        super().__init__(*args, **kwargs)

        # Limit category queryset to the given business to avoid cross-business selection
        if "category" in self.fields:
            if business is not None:
                self.fields["category"].queryset = Category.objects.filter(
                    business=business
                ).order_by("name")
            else:
                self.fields["category"].queryset = Category.objects.none()

            # make category not required because user may create new_category
            self.fields["category"].required = False

    def clean_price_per_unit(self):
        val = self.cleaned_data.get("price_per_unit")
        if val is None or val < Decimal("0.00"):
            raise ValidationError("Price must be non-negative.")
        return val

    def clean_stock_qty(self):
        val = self.cleaned_data.get("stock_qty")
        if val is None or val < Decimal("0.00"):
            raise ValidationError("Stock quantity must be non-negative.")
        return val

    def clean_new_category(self):
        # normalize whitespace
        val = (self.cleaned_data.get("new_category") or "").strip()
        return val

    def clean(self):
        cleaned = super().clean()
        # If both an existing category is selected and new_category is provided, prefer existing category.
        if cleaned.get("category") and cleaned.get("new_category"):
            cleaned["new_category"] = ""
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        """
        If new_category provided, create or reuse Category for the same business.
        This expects that either:
          - product.business will be set by the view before calling save(commit=False)
          - OR the form was instantiated with business=... so we can use self.business
        """
        new_cat_name = (self.cleaned_data.get("new_category") or "").strip()
        product = super().save(commit=False)

        # Determine business context: prefer product.business, fallback to form.business
        business = getattr(product, "business", None) or self.business

        if new_cat_name:
            if not business:
                raise ValidationError(
                    "Cannot create category without business context."
                )
            category_obj, created = Category.objects.get_or_create(
                business=business, name=new_cat_name
            )
            product.category = category_obj

        # Ensure product has business set if provided via form
        if not getattr(product, "business", None) and self.business:
            product.business = self.business

        if commit:
            product.save()
            # save_m2m is harmless here (no m2m defined) but call for completeness
            try:
                self.save_m2m()
            except Exception:
                pass

        return product


class PriceCalculatorForm(forms.Form):
    """
    Simple stateless calculator: provide a product (instance), quantity and unit,
    and it will compute the total price using product.price_for().
    """

    quantity = forms.DecimalField(
        max_digits=14,
        decimal_places=3,
        min_value=Decimal("0.001"),
        error_messages={"min_value": "Quantity must be greater than zero."},
        widget=forms.NumberInput(
            attrs={"step": "0.001", "class": "form-control form-control-sm"}
        ),
    )
    unit = forms.ChoiceField(
        choices=Product.UNIT_CHOICES,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )

    def __init__(self, *args, product: Optional[Product] = None, **kwargs):
        """
        Pass product instance optionally:
            form = PriceCalculatorForm(data=request.POST, product=product)
        """
        self.product = product
        super().__init__(*args, **kwargs)
        if self.product:
            # default unit to product.base_unit for UX convenience
            self.fields["unit"].initial = self.product.base_unit

    def clean(self):
        cleaned = super().clean()
        qty = cleaned.get("quantity")
        unit = cleaned.get("unit")

        if qty is None or unit is None:
            return cleaned

        if not self.product:
            raise ValidationError("Product is required to calculate price.")

        # check unit conversion support and compute price
        try:
            total_price = self.product.price_for(qty, unit)
        except (ValueError, InvalidOperation) as e:
            raise ValidationError(f"Cannot calculate price: {e}")

        cleaned["total_price"] = total_price
        return cleaned

    def get_total(self) -> Decimal:
        """
        Return computed total price (Decimal). Call after is_valid().
        """
        return self.cleaned_data.get("total_price", Decimal("0.00"))


class AddToCartForm(forms.Form):
    """
    A form to add a product to cart / create an invoice line.
    """

    product_id = forms.IntegerField(widget=forms.HiddenInput)
    quantity = forms.DecimalField(
        max_digits=14,
        decimal_places=3,
        min_value=Decimal("0.001"),
        widget=forms.NumberInput(
            attrs={"step": "0.001", "class": "form-control form-control-sm"}
        ),
    )
    unit = forms.ChoiceField(
        choices=Product.UNIT_CHOICES,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )

    def __init__(self, *args, product: Optional[Product] = None, **kwargs):
        """
        Optionally pass product instance (recommended).
        """
        self._product = product
        super().__init__(*args, **kwargs)
        if self._product:
            self.fields["product_id"].initial = self._product.pk
            self.fields["unit"].initial = self._product.base_unit

    def clean_product_id(self):
        pid = self.cleaned_data.get("product_id")
        if self._product and pid != self._product.pk:
            raise ValidationError("Product mismatch.")
        return pid

    def clean(self):
        cleaned = super().clean()
        qty = cleaned.get("quantity")
        unit = cleaned.get("unit")

        if qty is None or unit is None:
            return cleaned

        if not self._product:
            # resolve product if product_id was provided (optional - implement as needed in view)
            try:
                self._product = Product.objects.get(pk=cleaned.get("product_id"))
            except Product.DoesNotExist:
                raise ValidationError("Product not found.")

        # Validate unit conversion and stock availability
        try:
            # This will raise ValueError if conversion not supported
            qty_in_base = self._product._to_base_quantity(
                qty, unit, self._product.base_unit
            )
        except (ValueError, InvalidOperation) as e:
            raise ValidationError(f"Invalid unit/quantity: {e}")

        if self._product.stock_qty is not None:
            if qty_in_base > self._product.stock_qty:
                raise ValidationError(
                    f"Insufficient stock. Requested {qty} {unit} = {qty_in_base} {self._product.base_unit}, "
                    f"but only {self._product.stock_qty} {self._product.base_unit} available."
                )

        # compute line total and attach to cleaned_data
        try:
            line_total = self._product.price_for(qty, unit)
        except Exception as e:
            raise ValidationError(f"Could not compute price: {e}")

        cleaned["line_total"] = line_total
        cleaned["qty_in_base"] = qty_in_base
        return cleaned

    def get_line_total(self) -> Decimal:
        return self.cleaned_data.get("line_total", Decimal("0.00"))

    def apply(self, reduce_stock: bool = True) -> dict:
        """
        Apply this form: optionally reduce product.stock_qty by requested amount (converted to base unit).
        Does NOT call product.save() — the caller should persist changes inside a transaction.
        Returns a dict with keys:
            - product (Product instance)
            - qty_in_base (Decimal)
            - line_total (Decimal)
        Call only after is_valid().
        """
        if not self.is_valid():
            raise RuntimeError("Form must be valid before applying.")

        product = self._product
        qty_in_base = self.cleaned_data["qty_in_base"]
        line_total = self.cleaned_data["line_total"]

        if reduce_stock:
            # subtract from product.stock_qty but don't save
            product.stock_qty = Decimal(product.stock_qty) - qty_in_base

        return {
            "product": product,
            "qty_in_base": qty_in_base,
            "line_total": line_total,
        }
