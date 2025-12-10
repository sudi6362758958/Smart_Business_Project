from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Invoice
from products.models import Product
from django.core.mail import send_mail
from django.conf import settings

@receiver(post_save, sender=Invoice)
def process_invoice_stock(sender, instance, created, **kwargs):
    # only process once
    if not instance.stock_processed:
        for item in instance.items.all():
            p = item.product
            p.stock_qty = p.stock_qty - item.quantity
            p.save()
            if p.stock_qty <= p.low_stock_threshold:
                # send simple low stock email
                try:
                    send_mail(
                        subject=f'Low Stock Alert: {p.name}',
                        message=f'{p.name} stock is low ({p.stock_qty}).',
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[instance.business.email],
                        fail_silently=True,
                    )
                except Exception:
                    pass
        instance.stock_processed = True
        instance.save()
