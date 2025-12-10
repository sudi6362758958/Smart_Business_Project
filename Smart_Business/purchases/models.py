# market/purchase/models.py
from decimal import Decimal
from django.db import models, transaction
from django.core.exceptions import ValidationError

from accounts.models import Business
from products.models import Product, StockTransaction
from django.core.validators import RegexValidator


class Purchase(models.Model):
    business = models.ForeignKey(
        Business, on_delete=models.CASCADE, related_name="purchases"
    )
    supplier = models.CharField(max_length=200)
    company = models.CharField(max_length=200, blank=True, null=True)  # 🔹 add
    phone = models.CharField(
        max_length=10,
        validators=[
            RegexValidator(
                regex=r"^\d{10}$", message="Phone number must be exactly 10 digits."
            )
        ],
        blank=True,
        null=True,
    )
    date = models.DateField()
    total = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Purchase {self.pk} - {self.supplier} ({self.date})"


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(
        Purchase, on_delete=models.CASCADE, related_name="items"
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    # quantity is stored in product.base_unit (e.g., kg/ltr/pcs)
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.product.name} +{self.quantity} {self.product.base_unit}"

    def save(self, *args, **kwargs):
        """
        On create -> increase stock
        On update -> apply delta (supports product changes)
        """
        # Use atomic to avoid race conditions
        with transaction.atomic():
            is_new = self.pk is None

            # Capture old state (if any) BEFORE saving
            old_qty = Decimal("0")
            old_product_id = None
            if not is_new:
                # lock the old row for safe read
                old_obj = (
                    PurchaseItem.objects.select_for_update().only("quantity", "product").get(pk=self.pk)
                )
                old_qty = Decimal(old_obj.quantity or Decimal("0"))
                old_product_id = old_obj.product_id

            new_qty = Decimal(self.quantity or Decimal("0"))
            new_product_id = self.product_id

            # Save the PurchaseItem (this might create or update)
            super().save(*args, **kwargs)

            # Now compute deltas and apply to products
            # Case A: new item (old_qty == 0)
            # Case B: same product edited -> delta applies to that product
            # Case C: product changed -> remove old_qty from old product and add new_qty to new product
            if is_new:
                if new_qty != 0:
                    prod = Product.objects.select_for_update().get(pk=new_product_id)
                    prod.increase_stock(new_qty)
                    StockTransaction.objects.create(
                        product=prod,
                        qty_in_base=Decimal(new_qty),
                        source_type="purchase",
                        source_pk=self.pk,
                    )
            else:
                # edit case
                if old_product_id == new_product_id:
                    delta = new_qty - old_qty
                    if delta != 0:
                        prod = Product.objects.select_for_update().get(pk=new_product_id)
                        if delta > 0:
                            prod.increase_stock(delta)
                        else:
                            # delta negative -> reduce stock by -delta
                            prod.reduce_stock(-delta)
                        StockTransaction.objects.create(
                            product=prod,
                            qty_in_base=Decimal(delta),
                            source_type="purchase",
                            source_pk=self.pk,
                        )
                else:
                    # product changed: reverse old product, apply to new product
                    if old_qty != 0 and old_product_id is not None:
                        old_prod = Product.objects.select_for_update().get(pk=old_product_id)
                        # remove previously added qty from old product
                        old_prod.reduce_stock(old_qty)
                        StockTransaction.objects.create(
                            product=old_prod,
                            qty_in_base=Decimal(-old_qty),
                            source_type="purchase",
                            source_pk=self.pk,
                        )
                    if new_qty != 0:
                        new_prod = Product.objects.select_for_update().get(pk=new_product_id)
                        new_prod.increase_stock(new_qty)
                        StockTransaction.objects.create(
                            product=new_prod,
                            qty_in_base=Decimal(new_qty),
                            source_type="purchase",
                            source_pk=self.pk,
                        )

    def delete(self, *args, **kwargs):
        """
        On delete -> subtract previously added quantity (reverse the purchase item)
        """
        with transaction.atomic():
            # Lock the product row and apply reverse change atomically
            prod = Product.objects.select_for_update().get(pk=self.product.pk)
            old_qty = Decimal(self.quantity or Decimal("0"))

            if old_qty != 0:
                # Removing a purchase item should subtract the amount it had previously added.
                # i.e., if purchase added +10, deleting it should do -10.
                prod.reduce_stock(old_qty)
                StockTransaction.objects.create(
                    product=prod,
                    qty_in_base=Decimal(-old_qty),
                    source_type="purchase",
                    source_pk=self.pk,
                )

            # Finally remove the PurchaseItem record
            super().delete(*args, **kwargs)
