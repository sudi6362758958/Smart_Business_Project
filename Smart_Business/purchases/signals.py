from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Purchase

@receiver(post_save, sender=Purchase)
def process_purchase_stock(sender, instance, created, **kwargs):
    # add incoming stock
    for item in instance.items.all():
        p = item.product
        p.stock_qty = p.stock_qty + item.quantity
        p.save()
