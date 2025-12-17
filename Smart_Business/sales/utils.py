from io import BytesIO
from decimal import Decimal
from django.template.loader import get_template
from xhtml2pdf import pisa
import os
from django.conf import settings

def link_callback(uri, rel):
    """
    Convert static/media URIs to absolute file paths so xhtml2pdf can access them
    """
    if uri.startswith(settings.STATIC_URL):
        path = os.path.join(
            settings.STATIC_ROOT,
            uri.replace(settings.STATIC_URL, "")
        )
    elif uri.startswith(settings.MEDIA_URL):
        path = os.path.join(
            settings.MEDIA_ROOT,
            uri.replace(settings.MEDIA_URL, "")
        )
    else:
        return uri

    if not os.path.isfile(path):
        raise Exception(f"Media URI not found: {path}")

    return path


def render_invoice_pdf(invoice):
    """
    Returns PDF bytes for an invoice.
    Used by BOTH browser PDF & email.
    """

    rows = []
    subtotal_taxable = Decimal("0.00")
    subtotal_exempt = Decimal("0.00")

    CGST_RATE = Decimal("0.09")   # 9%
    SGST_RATE = Decimal("0.09")   # 9%

    for item in invoice.items.all():
        line = item.line_total
        subtotal_taxable += line

        cgst = (line * CGST_RATE).quantize(Decimal("0.01"))
        sgst = (line * SGST_RATE).quantize(Decimal("0.01"))

        rows.append({
            "product": item.product.name,
            "uom": item.product.base_unit,
            "qty": item.quantity,
            "price": item.unit_price,
            "line": line,
            "cgst": cgst,
            "sgst": sgst,
        })

    cgst_sum = (subtotal_taxable * CGST_RATE).quantize(Decimal("0.01"))
    sgst_sum = (subtotal_taxable * SGST_RATE).quantize(Decimal("0.01"))
    total_with_tax = subtotal_taxable + cgst_sum + sgst_sum
    qr_path = os.path.join(settings.STATIC_ROOT, "qr", "payment_qr.jpeg")
    

    context = {
        "invoice": invoice,
        "rows": rows,

        # summary
        "subtotal_taxable": subtotal_taxable,
        "subtotal_exempt": subtotal_exempt,
        "cgst_sum": cgst_sum,
        "sgst_sum": sgst_sum,
        "total_with_tax": total_with_tax,
        "qr_path": qr_path,
    }

    template = get_template("sales/invoice_pdf.html")
    html = template.render(context)

    pdf_file = BytesIO()
    pisa.CreatePDF(
    html,
    dest=pdf_file,
    link_callback=link_callback
    )

    return pdf_file

