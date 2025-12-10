# market/products/models.py
from decimal import Decimal, ROUND_HALF_UP
from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone

# NEW imports for atomic updates
from django.db import transaction
from django.db.models import F

QUANTIZE_EXP = Decimal("0.001")  # keep 3 decimals for stock precision


def quantize(amount: Decimal) -> Decimal:
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    return amount.quantize(QUANTIZE_EXP, rounding=ROUND_HALF_UP)


class Category(models.Model):
    business = models.ForeignKey(
        "accounts.Business", on_delete=models.CASCADE, related_name="categories"
    )
    name = models.CharField(max_length=100)

    class Meta:
        unique_together = ("business", "name")
        ordering = ("name",)

    def __str__(self):
        return self.name


class Product(models.Model):
    UNIT_KG = "kg"
    UNIT_G = "g"
    UNIT_PCS = "pcs"
    UNIT_LTR = "ltr"
    UNIT_ML = "ml"

    UNIT_CHOICES = [
        (UNIT_KG, "Kilogram"),
        (UNIT_G, "Gram"),
        (UNIT_PCS, "Piece"),
        (UNIT_LTR, "Litre"),
        (UNIT_ML, "Millilitre"),
    ]

    business = models.ForeignKey(
        "accounts.Business", on_delete=models.CASCADE, related_name="products"
    )
    category = models.CharField(max_length=100, blank=True, null=True)
    name = models.CharField(max_length=200)
    base_unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default=UNIT_PCS)
    price_per_unit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Price for 1 base unit (e.g., price per 1 kg if base_unit='kg')",
    )
    stock_qty = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
        help_text="Quantity available expressed in the base_unit",
    )
    low_stock_threshold = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("1.000"),
        validators=[MinValueValidator(Decimal("0.000"))],
        help_text="When stock_qty <= this, consider low stock",
    )

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name

    # ---------- defensive save ----------
    def save(self, *args, **kwargs):
        """
        Ensure stock_qty is quantized and never negative before saving.
        This adds a safety net for any code path that updates stock_qty
        without proper validation.
        """
        # Normalize and quantize stock_qty
        try:
            if self.stock_qty is None:
                self.stock_qty = Decimal("0.000")
            else:
                # ensure Decimal and quantize
                self.stock_qty = quantize(self.stock_qty)
        except Exception:
            self.stock_qty = Decimal("0.000")

        # Clamp to zero (no negative stock)
        if self.stock_qty < Decimal("0.000"):
            self.stock_qty = Decimal("0.000")

        # Also quantize low_stock_threshold defensively
        try:
            if self.low_stock_threshold is None:
                self.low_stock_threshold = Decimal("0.000")
            else:
                self.low_stock_threshold = quantize(self.low_stock_threshold)
        except Exception:
            self.low_stock_threshold = Decimal("0.000")

        super().save(*args, **kwargs)

    # ---------- unit conversion ----------
    @staticmethod
    def _to_base_quantity(qty: Decimal, from_unit: str, base_unit: str) -> Decimal:
        """
        Convert qty from `from_unit` to `base_unit`.
        Supported conversions:
          - g <-> kg
          - ml <-> ltr
        If from_unit == base_unit, returns qty unchanged.
        """
        qty = Decimal(qty)
        if from_unit == base_unit:
            return qty

        # grams -> kilograms
        if from_unit == Product.UNIT_G and base_unit == Product.UNIT_KG:
            return qty / Decimal("1000")
        if from_unit == Product.UNIT_KG and base_unit == Product.UNIT_G:
            return qty * Decimal("1000")

        # millilitre <-> litre
        if from_unit == Product.UNIT_ML and base_unit == Product.UNIT_LTR:
            return qty / Decimal("1000")
        if from_unit == Product.UNIT_LTR and base_unit == Product.UNIT_ML:
            return qty * Decimal("1000")

        # Conversion not supported
        raise ValueError(f"Unsupported conversion {from_unit} -> {base_unit}")

    # ---------- pricing ----------
    def price_for(self, quantity: Decimal, unit: str) -> Decimal:
        qty = Decimal(quantity)
        if qty <= 0:
            return quantize(Decimal("0.00"))
        qty_in_base = self._to_base_quantity(qty, unit, self.base_unit)
        total = Decimal(self.price_per_unit) * qty_in_base
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # ---------- stock helpers ----------
    def increase_stock(self, qty_in_base: Decimal):
        """Increase stock_qty by given amount (qty_in_base already in base_unit)."""
        qty = Decimal(qty_in_base)
        if qty == 0:
            return

        # Atomically update the DB using F-expression to avoid race conditions.
        # Then refresh and quantize the value via save().
        with transaction.atomic():
            # Lock this product row to be safe for reading/writing in this tx
            Product.objects.select_for_update().filter(pk=self.pk)
            Product.objects.filter(pk=self.pk).update(stock_qty=F("stock_qty") + qty)
            # Refresh instance from DB (new stock value)
            self.refresh_from_db(fields=["stock_qty"])

            # Ensure quantization and non-negative constraint (defensive)
            if self.stock_qty is None:
                self.stock_qty = Decimal("0.000")
            # Clamp to zero if somehow negative occurred
            if self.stock_qty < Decimal("0.000"):
                self.stock_qty = Decimal("0.000")
            # Quantize then persist the quantized value (this will not change numeric value unless rounding is needed)
            self.stock_qty = quantize(self.stock_qty)
            # Save the (possibly adjusted) quantized/clamped value back
            super(Product, self).save(update_fields=["stock_qty"])

    def reduce_stock(self, qty_in_base: Decimal):
        """
        Reduce stock by qty_in_base. If qty_in_base is greater than available stock,
        we clamp stock to zero (do not allow negative stock). This avoids negative
        values and keeps behavior predictable for the UI.

        Note: Upper layers (invoices) should perform validation and prevent
        creating an invoice with qty > stock if they want to block that action.
        """
        qty = Decimal(qty_in_base)
        if qty == 0:
            return

        with transaction.atomic():
            # Lock for update and apply delta atomically
            Product.objects.select_for_update().filter(pk=self.pk)
            # Calculate new value at DB level: stock_qty = stock_qty - qty
            Product.objects.filter(pk=self.pk).update(stock_qty=F("stock_qty") - qty)
            # Refresh instance value
            self.refresh_from_db(fields=["stock_qty"])

            # Clamp to zero if negative and quantize
            if self.stock_qty is None:
                self.stock_qty = Decimal("0.000")
            if self.stock_qty < Decimal("0.000"):
                self.stock_qty = Decimal("0.000")
            self.stock_qty = quantize(self.stock_qty)
            super(Product, self).save(update_fields=["stock_qty"])

    def remaining_stock_display(self) -> str:
        """
        Return a nicely formatted stock string:
         - Whole numbers are shown as integers: 0.000 -> "0", 100.000 -> "100"
         - Non-whole numbers show up to 3 decimals trimmed: 1.250 -> "1.25"
        """
        qty = quantize(Decimal(self.stock_qty or Decimal("0.000")))
        # If it's a whole number, show as integer
        if qty == qty.to_integral_value():
            return f"{int(qty)} {self.base_unit}"
        # Otherwise, format and trim trailing zeros
        s = format(qty, "f").rstrip("0").rstrip(".")
        return f"{s} {self.base_unit}"

    def is_low_stock(self) -> bool:
        return Decimal(self.stock_qty or Decimal("0.000")) <= Decimal(self.low_stock_threshold or Decimal("0.000"))


class StockTransaction(models.Model):
    """
    Optional audit trail for stock movements.
    qty_in_base: positive for IN (purchase), negative for OUT (sale).
    source_type: 'purchase' or 'sale' (invoice)
    """

    IN = "in"
    OUT = "out"
    SOURCE_CHOICES = [
        ("purchase", "Purchase"),
        ("sale", "Sale"),
        ("manual", "Manual"),
    ]

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="stock_txns"
    )
    qty_in_base = models.DecimalField(max_digits=14, decimal_places=3)
    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    source_pk = models.IntegerField(
        null=True,
        blank=True,
        help_text="PK of source model (PurchaseItem or InvoiceItem)",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at",)
