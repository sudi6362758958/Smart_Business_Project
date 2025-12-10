# market/sales/models.py
from decimal import Decimal, ROUND_HALF_UP
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import Business
from products.models import Product, StockTransaction

QUANTIZE_EXP = Decimal("0.01")  # invoice/totals round to 2 decimals


def quantize(amount: Decimal) -> Decimal:
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    return amount.quantize(QUANTIZE_EXP, rounding=ROUND_HALF_UP)


class Customer(models.Model):
    business = models.ForeignKey(
        Business, on_delete=models.CASCADE, related_name="customers"
    )
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    address = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name


class Invoice(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PARTIAL = "partial"
    STATUS_PAID = "paid"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PARTIAL, "Partial"),
        (STATUS_PAID, "Paid"),
    ]

    business = models.ForeignKey(
        Business, on_delete=models.CASCADE, related_name="invoices"
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    invoice_no = models.CharField(max_length=50, unique=True, blank=True)
    date = models.DateField(default=timezone.now)

    subtotal_taxable = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    subtotal_exempt = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    cgst_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    sgst_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    tax_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )

    amount_paid = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING
    )

    paid = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    stock_processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.invoice_no or f"Invoice {self.pk}"

    @property
    def remaining_amount(self) -> Decimal:
        rem = quantize(Decimal(self.total) - Decimal(self.amount_paid))
        return rem if rem >= Decimal("0.00") else Decimal("0.00")

    def _compute_status_from_amounts(self):
        total = quantize(Decimal(self.total))
        paid = quantize(Decimal(self.amount_paid))

        if total <= Decimal("0.00") and paid <= Decimal("0.00"):
            return self.STATUS_PAID
        if paid <= Decimal("0.00"):
            return self.STATUS_PENDING
        if paid < total:
            return self.STATUS_PARTIAL
        return self.STATUS_PAID

    def recalc_totals(self):
        items = self.items.all()

        subtotal_taxable = Decimal("0.00")
        subtotal_exempt = Decimal("0.00")
        cgst_total = Decimal("0.00")
        sgst_total = Decimal("0.00")
        tax_total = Decimal("0.00")
        total = Decimal("0.00")

        for it in items:
            subtotal = Decimal(it.line_total)
            if it.tax_percent and Decimal(it.tax_percent) != Decimal("0"):
                subtotal_taxable += subtotal
            else:
                subtotal_exempt += subtotal

            cgst_total += Decimal(it.cgst_amount or 0)
            sgst_total += Decimal(it.sgst_amount or 0)
            tax_total += Decimal(it.tax_amount or 0)
            total += subtotal + Decimal(it.tax_amount or 0)

        self.subtotal_taxable = quantize(subtotal_taxable)
        self.subtotal_exempt = quantize(subtotal_exempt)
        self.cgst_total = quantize(cgst_total)
        self.sgst_total = quantize(sgst_total)
        self.tax_total = quantize(tax_total)
        self.total = quantize(total)

        payments_sum = self.payments.aggregate(s=Sum("amount"))["s"]
        if payments_sum is not None:
            self.amount_paid = quantize(Decimal(payments_sum))

        self.status = self._compute_status_from_amounts()
        self.paid = self.status == self.STATUS_PAID

    def _generate_invoice_no(self):
        today_str = timezone.now().strftime("%Y%m%d")
        prefix = today_str + "-"
        qs = Invoice.objects.filter(
            business=self.business, invoice_no__startswith=prefix
        ).values_list("invoice_no", flat=True)
        max_seq = 0
        for inv_no in qs:
            try:
                seq_str = inv_no.split("-", 1)[1]
                seq_num = int(seq_str)
                if seq_num > max_seq:
                    max_seq = seq_num
            except Exception:
                continue
        next_seq = max_seq + 1
        return f"{prefix}{next_seq:03d}"

    def save(self, *args, **kwargs):
        if not self.invoice_no:
            if not self.business_id:
                raise ValueError("Business must be set before generating invoice_no")
            self.invoice_no = self._generate_invoice_no()

        super().save(*args, **kwargs)
        # recalc totals & persist aggregated fields
        self.recalc_totals()
        Invoice.objects.filter(pk=self.pk).update(
            subtotal_taxable=self.subtotal_taxable,
            subtotal_exempt=self.subtotal_exempt,
            cgst_total=self.cgst_total,
            sgst_total=self.sgst_total,
            tax_total=self.tax_total,
            total=self.total,
            amount_paid=self.amount_paid,
            status=self.status,
            paid=self.paid,
        )


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    # uom is multiplier in product.base_unit (e.g., 0.5 for 0.5 Ltr)
    uom = models.DecimalField(max_digits=10, decimal_places=3, default=Decimal("1.000"))
    quantity = models.DecimalField(
        max_digits=14, decimal_places=3, default=Decimal("1.000")
    )
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )

    tax_percent = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0.00")
    )

    line_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    tax_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    cgst_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    sgst_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"

    def _total_in_base(self, uom: Decimal, qty: Decimal) -> Decimal:
        """
        Convert uom * qty into product.base_unit quantity (Decimal).
        Here we assume uom and qty are already in the same dimension as product.base_unit
        (e.g., if base_unit is 'kg' the uom is specified in kg, like 0.5).
        If you need to support mixed units (e.g., uom unit different), update signature.
        """
        return quantize(Decimal(uom) * Decimal(qty))

    def recalc_line(self):
        uom = Decimal(self.uom or 1)
        qty = Decimal(self.quantity)
        price = Decimal(self.unit_price)
        tax_pct = Decimal(self.tax_percent or 0)

        line = uom * qty * price
        self.line_total = quantize(line)

        tax_amount = (self.line_total * tax_pct) / Decimal("100.00")
        self.tax_amount = quantize(tax_amount)
        self.cgst_amount = quantize(self.tax_amount / Decimal("2.00"))
        self.sgst_amount = quantize(self.tax_amount - self.cgst_amount)

    def save(self, *args, **kwargs):
        """
        On create -> reduce product stock by uom*quantity
        On update -> compute delta and apply to stock
        """
        with transaction.atomic():
            is_new = self.pk is None
            old_total_base = Decimal("0")
            old_product_id = None
            if not is_new:
                old = InvoiceItem.objects.select_for_update().get(pk=self.pk)
                old_total_base = self._total_in_base(old.uom, old.quantity)
                old_product_id = old.product_id

            new_total_base = self._total_in_base(self.uom, self.quantity)

            # compute line amounts before super().save() to ensure consistency
            self.recalc_line()

            super().save(*args, **kwargs)

            # If product changed: revert old product stock then apply to new product
            if old_product_id and old_product_id != self.product_id:
                old_prod = Product.objects.select_for_update().get(pk=old_product_id)
                # Revert previous OUT (sale) by adding back old_total_base
                old_prod.increase_stock(old_total_base)
                StockTransaction.objects.create(
                    product=old_prod,
                    qty_in_base=Decimal(old_total_base),
                    source_type="sale",
                    source_pk=self.pk,
                )

            # Now apply delta on current product atomically via product helpers
            prod = Product.objects.select_for_update().get(pk=self.product.pk)
            delta = new_total_base - old_total_base
            # delta > 0 : more quantity sold than before -> reduce stock by delta
            # delta < 0 : less quantity sold -> add back (-delta)
            if delta > 0:
                prod.reduce_stock(delta)
                StockTransaction.objects.create(
                    product=prod,
                    qty_in_base=-Decimal(delta),
                    source_type="sale",
                    source_pk=self.pk,
                )
            elif delta < 0:
                prod.increase_stock(-delta)
                StockTransaction.objects.create(
                    product=prod,
                    qty_in_base=Decimal(-delta),
                    source_type="sale",
                    source_pk=self.pk,
                )

            # after changing item, update invoice aggregates
            if self.invoice_id:
                inv = Invoice.objects.select_for_update().get(pk=self.invoice_id)
                inv.recalc_totals()
                Invoice.objects.filter(pk=inv.pk).update(
                    subtotal_taxable=inv.subtotal_taxable,
                    subtotal_exempt=inv.subtotal_exempt,
                    cgst_total=inv.cgst_total,
                    sgst_total=inv.sgst_total,
                    tax_total=inv.tax_total,
                    total=inv.total,
                    amount_paid=inv.amount_paid,
                    status=inv.status,
                    paid=inv.paid,
                )

    def delete(self, *args, **kwargs):
        """
        When invoice item deleted, add back the quantity to product stock.
        """
        with transaction.atomic():
            prod = Product.objects.select_for_update().get(pk=self.product.pk)
            qty_base = self._total_in_base(self.uom, self.quantity)
            # Deleting invoice item returns the sold qty back to stock
            prod.increase_stock(qty_base)
            StockTransaction.objects.create(
                product=prod,
                qty_in_base=qty_base,
                source_type="sale",
                source_pk=self.pk,
            )
            super().delete(*args, **kwargs)


class Payment(models.Model):
    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    payment_date = models.DateTimeField(default=timezone.now)
    method = models.CharField(max_length=100, blank=True, default="")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment {self.pk} - {self.amount} on {self.payment_date:%Y-%m-%d}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.invoice_id:
            inv = Invoice.objects.select_for_update().get(pk=self.invoice_id)
            inv.recalc_totals()
            Invoice.objects.filter(pk=inv.pk).update(
                amount_paid=inv.amount_paid, status=inv.status, paid=inv.paid
            )

    def delete(self, *args, **kwargs):
        invoice_id = self.invoice_id
        super().delete(*args, **kwargs)
        if invoice_id:
            inv = Invoice.objects.select_for_update().get(pk=invoice_id)
            inv.recalc_totals()
            Invoice.objects.filter(pk=inv.pk).update(
                amount_paid=inv.amount_paid, status=inv.status, paid=inv.paid
            )
