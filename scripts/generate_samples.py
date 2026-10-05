import os
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = BASE_DIR / "sample_data"
SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

def create_verified_po(output_path: Path):
    c = canvas.Canvas(str(output_path), pagesize=letter)
    width, height = letter

    # Header Banner
    c.setFillColor(colors.HexColor("#0f172a"))
    c.rect(0, height - 90, width, 90, fill=True, stroke=False)
    
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(40, height - 50, "PURCHASE ORDER")
    c.setFont("Helvetica", 10)
    c.drawString(40, height - 70, "Apex Precision Components Ltd. | High-Spec Industrial Parts")

    c.drawRightString(width - 40, height - 45, "PO NUMBER: PO-98241")
    c.drawRightString(width - 40, height - 62, "DATE: 2026-10-04")
    c.drawRightString(width - 40, height - 77, "TERMS: Net 30 Days")

    # Vendor & Buyer
    c.setFillColor(colors.HexColor("#1e293b"))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, height - 125, "VENDOR:")
    c.drawString(320, height - 125, "SHIP TO:")

    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#334155"))
    c.drawString(40, height - 140, "Vanguard Tooling & Supply Co.")
    c.drawString(40, height - 153, "5800 Innovation Way, Suite 200")
    c.drawString(40, height - 166, "Austin, TX 78744")
    c.drawString(40, height - 179, "Email: orders@vanguardtooling.com | Tax ID: 84-2910384")

    c.drawString(320, height - 140, "Apex Precision - Receiving Dock 4")
    c.drawString(320, height - 153, "1200 Logistics Blvd")
    c.drawString(320, height - 166, "Cleveland, OH 44114")
    c.drawString(320, height - 179, "Attn: Procurement & Ops Team")

    # Table Header
    y = height - 220
    c.setFillColor(colors.HexColor("#f1f5f9"))
    c.rect(40, y, width - 80, 22, fill=True, stroke=False)

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, y + 6, "#")
    c.drawString(80, y + 6, "SKU")
    c.drawString(170, y + 6, "Description")
    c.drawRightString(390, y + 6, "Qty")
    c.drawRightString(470, y + 6, "Unit Price")
    c.drawRightString(width - 50, y + 6, "Total")

    # Table Items
    items = [
        ("1", "HYD-4001", "Heavy Duty Hydraulic Hose 1/2in x 50ft", "3", "$145.00", "$435.00"),
        ("2", "VAL-8820", "Brass 2-Way Industrial Ball Valve 1-inch NPT", "5", "$34.50", "$172.50"),
        ("3", "GSK-1104", "Flange Gasket Nitrile High-Pressure 150#", "10", "$8.20", "$82.00"),
    ]

    c.setFont("Helvetica", 9)
    for idx, (num, sku, desc, qty, price, total) in enumerate(items):
        y -= 25
        if idx % 2 == 1:
            c.setFillColor(colors.HexColor("#f8fafc"))
            c.rect(40, y - 6, width - 80, 22, fill=True, stroke=False)
        c.setFillColor(colors.HexColor("#1e293b"))
        c.drawString(50, y, num)
        c.drawString(80, y, sku)
        c.drawString(170, y, desc)
        c.drawRightString(390, y, qty)
        c.drawRightString(470, y, price)
        c.drawRightString(width - 50, y, total)

    # Summary Box
    y -= 50
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.line(320, y + 30, width - 40, y + 30)

    c.setFont("Helvetica", 9)
    c.drawString(340, y + 10, "Subtotal:")
    c.drawRightString(width - 50, y + 10, "$689.50")

    c.drawString(340, y - 8, "Tax (8.25%):")
    c.drawRightString(width - 50, y - 8, "$56.88")

    c.drawString(340, y - 26, "Shipping & Freight:")
    c.drawRightString(width - 50, y - 26, "$35.00")

    # Grand Total
    c.setFillColor(colors.HexColor("#f0fdf4"))
    c.rect(330, y - 55, width - 370, 22, fill=True, stroke=False)
    c.setFillColor(colors.HexColor("#166534"))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(340, y - 48, "Grand Total:")
    c.drawRightString(width - 50, y - 48, "$781.38")

    # Notes
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(40, 60, "Please verify PO-98241 on all shipping crates and delivery manifests. Payment via standard ACH.")
    
    c.save()
    print(f"Generated verified PO: {output_path}")

def create_discrepancy_invoice(output_path: Path):
    c = canvas.Canvas(str(output_path), pagesize=letter)
    width, height = letter

    # Header Banner
    c.setFillColor(colors.HexColor("#431407"))
    c.rect(0, height - 90, width, 90, fill=True, stroke=False)
    
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(40, height - 50, "SUPPLIER INVOICE")
    c.setFont("Helvetica", 10)
    c.drawString(40, height - 70, "Midwest Heavy Hydraulics Corp.")

    c.drawRightString(width - 40, height - 45, "INVOICE #: INV-30491")
    c.drawRightString(width - 40, height - 62, "DATE: 2026-09-28")
    c.drawRightString(width - 40, height - 77, "TERMS: Due Upon Receipt")

    # Vendor & Customer
    c.setFillColor(colors.HexColor("#1e293b"))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, height - 125, "FROM:")
    c.drawString(320, height - 125, "BILLED TO:")

    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#334155"))
    c.drawString(40, height - 140, "Midwest Heavy Hydraulics Corp.")
    c.drawString(40, height - 153, "900 Industrial Parkway, Chicago, IL")

    c.drawString(320, height - 140, "Apex Precision Components Ltd.")
    c.drawString(320, height - 153, "Accounts Payable, Cleveland, OH")

    # Table Header
    y = height - 220
    c.setFillColor(colors.HexColor("#f1f5f9"))
    c.rect(40, y, width - 80, 22, fill=True, stroke=False)

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, y + 6, "#")
    c.drawString(80, y + 6, "SKU")
    c.drawString(170, y + 6, "Description")
    c.drawRightString(390, y + 6, "Qty")
    c.drawRightString(470, y + 6, "Unit Price")
    c.drawRightString(width - 50, y + 6, "Total")

    # Intentional math error on line 1: 4 * 125.00 = 500, but stated 580.00!
    items = [
        ("1", "IND-100", "Industrial Grade Ball Bearings (Set of 10)", "4", "$125.00", "$580.00"),
        ("2", "LUB-205", "High-Temp Synthetic Lubricant 5L", "2", "$45.50", "$91.00"),
    ]

    c.setFont("Helvetica", 9)
    for idx, (num, sku, desc, qty, price, total) in enumerate(items):
        y -= 25
        c.drawString(50, y, num)
        c.drawString(80, y, sku)
        c.drawString(170, y, desc)
        c.drawRightString(390, y, qty)
        c.drawRightString(470, y, price)
        c.drawRightString(width - 50, y, total)

    # Summary
    y -= 50
    c.drawString(340, y + 10, "Subtotal:")
    c.drawRightString(width - 50, y + 10, "$671.00")

    c.drawString(340, y - 8, "Tax (8%):")
    c.drawRightString(width - 50, y - 8, "$53.68")

    c.drawString(340, y - 26, "Shipping:")
    c.drawRightString(width - 50, y - 26, "$25.00")

    c.setFont("Helvetica-Bold", 10)
    c.drawString(340, y - 48, "Grand Total:")
    c.drawRightString(width - 50, y - 48, "$749.68")

    c.save()
    print(f"Generated discrepancy invoice: {output_path}")

if __name__ == "__main__":
    create_verified_po(SAMPLES_DIR / "sample_po_verified.pdf")
    create_discrepancy_invoice(SAMPLES_DIR / "sample_invoice_discrepancy.pdf")
