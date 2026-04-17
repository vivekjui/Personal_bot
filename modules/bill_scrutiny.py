"""
Noting Bot - Module 6: Bill Scrutiny & Document Generation
Scrutinizes contractor bills using Gemini AI and generates forwarding letter,
sanction letter, and account-of-work register.
"""

import json
from pathlib import Path
from modules.utils import (CONFIG, logger, ask_gemini, extract_text_from_pdf,
                           get_case_folder, create_docx_from_text,
                           fill_docx_template, sanitize_filename, today_str)
from modules.database import add_bill


TEMPLATES_DIR = Path(CONFIG["paths"]["templates_dir"])


def scrutinize_bill(bill_pdf_path: str, contract_details: dict = None) -> dict:
    """
    Use Gemini AI to scrutinize a contractor bill.

    contract_details: dict with keys like 'work_order_amount', 'work_order_date',
                      'contractor_name', 'contract_value', 'previous_bills_paid'

    Returns a dict with extracted data, discrepancies, and recommendation.
    """
    logger.info(f"Scrutinizing bill: {bill_pdf_path}")
    bill_text = extract_text_from_pdf(bill_pdf_path)

    context = ""
    if contract_details:
        context = f"""
CONTRACT DETAILS FOR CROSS-CHECKING:
- Work Order No.: {contract_details.get('work_order_no', 'N/A')}
- Work Order Date: {contract_details.get('work_order_date', 'N/A')}
- Contract Value: Rs. {contract_details.get('contract_value', 0):,}
- Contractor Name: {contract_details.get('contractor_name', 'N/A')}
- Previous Amount Paid: Rs. {contract_details.get('previous_bills_paid', 0):,}
"""

    prompt = f"""You are a senior accounts officer scrutinizing a contractor's bill in an Indian government department.
{context}

BILL DOCUMENT TEXT:
{bill_text[:6000]}

Analyze the bill and provide a JSON response in this EXACT format:
{{
  "contractor_name": "extracted contractor name",
  "bill_no": "bill number",
  "bill_date": "date of bill",
  "gross_amount": 0.0,
  "deductions": {{
    "income_tax": 0.0,
    "gst_tds": 0.0,
    "security_deposit": 0.0,
    "labour_cess": 0.0,
    "other": 0.0,
    "total_deductions": 0.0
  }},
  "net_payable": 0.0,
  "discrepancies": ["list any issues found"],
  "status": "APPROVED / OBJECTION RAISED / REFER BACK",
  "remarks": "overall remarks"
}}

Return ONLY the JSON."""

    try:
        response = ask_gemini(prompt)
        response = response.strip().strip("```json").strip("```").strip()
        result = json.loads(response)
        result["bill_pdf"] = bill_pdf_path
        return result
    except Exception as e:
        logger.warning(f"Bill scrutiny parse error: {e}")
        return {
            "status": "Review Required",
            "raw_analysis": ask_gemini(f"Summarize this contractor bill:\n{bill_text[:3000]}"),
            "bill_pdf": bill_pdf_path
        }


def generate_bill_forwarding_letter(
    case_id: str,
    bill_data: dict,
    case_name: str,
    work_order_no: str = "",
    officer_name: str = "",
    officer_designation: str = ""
) -> str:
    """Generate bill forwarding letter DOCX. Returns output file path."""
    template_path = TEMPLATES_DIR / "bill_forwarding_letter.docx"
    output_dir = get_case_folder(case_id, "Generated")
    filename = sanitize_filename(f"Bill_Forwarding_Letter_{bill_data.get('bill_no', today_str())}.docx")
    output_path = str(output_dir / filename)

    replacements = {
        "DATE": today_str(),
        "CASE_NAME": case_name,
        "WORK_ORDER_NO": work_order_no,
        "CONTRACTOR_NAME": bill_data.get("contractor_name", "_______________"),
        "BILL_NO": bill_data.get("bill_no", "_______________"),
        "BILL_DATE": bill_data.get("bill_date", "_______________"),
        "GROSS_AMOUNT": f"Rs. {bill_data.get('gross_amount', 0):,.0f}",
        "NET_PAYABLE": f"Rs. {bill_data.get('net_payable', 0):,.0f}",
        "OFFICER_NAME": officer_name,
        "OFFICER_DESIGNATION": officer_designation
    }

    if template_path.exists():
        fill_docx_template(str(template_path), replacements, output_path)
    else:
        # Generate from scratch if template missing
        content = f"""# BILL FORWARDING LETTER

**Date:** {today_str()}

To,
The {officer_designation or 'Accounts Officer'},
{officer_name or '_______________'}

**Subject:** Forwarding of Running Account Bill — {case_name}

Sir/Madam,

Please find enclosed herewith the Running Account Bill No. **{replacements['BILL_NO']}** dated **{replacements['BILL_DATE']}** 
submitted by **{replacements['CONTRACTOR_NAME']}** for the work **{case_name}** 
against Work Order No. **{work_order_no or '_______________'}** for payment.

Details of the bill are as under:

- **Gross Amount of Bill:** {replacements['GROSS_AMOUNT']}
- **Net Payable Amount:** {replacements['NET_PAYABLE']}

The bill has been scrutinized and is found to be in order. It is forwarded for necessary action and payment.

Yours faithfully,

_______________
{officer_designation or 'Dealing Hand'}
Date: {today_str()}
"""
        create_docx_from_text(content, output_path, "Bill Forwarding Letter")

    logger.info(f"Bill Forwarding Letter: {output_path}")
    return output_path


def generate_sanction_letter(
    case_id: str,
    bill_data: dict,
    case_name: str,
    work_order_no: str = "",
    sanctioning_authority: str = "",
    designation: str = ""
) -> str:
    """Generate sanction letter for payment. Returns output file path."""
    template_path = TEMPLATES_DIR / "sanction_letter.docx"
    output_dir = get_case_folder(case_id, "Generated")
    filename = sanitize_filename(f"Sanction_Letter_{bill_data.get('bill_no', today_str())}.docx")
    output_path = str(output_dir / filename)

    deductions = bill_data.get("deductions", {})

    replacements = {
        "DATE": today_str(),
        "CASE_NAME": case_name,
        "WORK_ORDER_NO": work_order_no,
        "CONTRACTOR_NAME": bill_data.get("contractor_name", "_______________"),
        "BILL_NO": bill_data.get("bill_no", "_______________"),
        "GROSS_AMOUNT": f"Rs. {bill_data.get('gross_amount', 0):,.0f}",
        "IT_DEDUCTION": f"Rs. {deductions.get('income_tax', 0):,.0f}",
        "GST_TDS": f"Rs. {deductions.get('gst_tds', 0):,.0f}",
        "SD_DEDUCTION": f"Rs. {deductions.get('security_deposit', 0):,.0f}",
        "TOTAL_DEDUCTIONS": f"Rs. {deductions.get('total_deductions', 0):,.0f}",
        "NET_PAYABLE": f"Rs. {bill_data.get('net_payable', 0):,.0f}",
        "AUTHORITY_NAME": sanctioning_authority,
        "AUTHORITY_DESIGNATION": designation
    }

    if template_path.exists():
        fill_docx_template(str(template_path), replacements, output_path)
    else:
        content = f"""# SANCTION LETTER / PAYMENT ORDER

**Sanction No.:** _______________
**Date:** {today_str()}

**Subject:** Sanction for Payment of Running Account Bill — {case_name}

In exercise of the powers vested, sanction is hereby accorded for payment of the 
following amount to **{replacements['CONTRACTOR_NAME']}** against Running Account Bill 
No. **{replacements['BILL_NO']}** for the work **{case_name}** (Work Order No. {work_order_no}):

| Particulars | Amount |
|-------------|--------|
| Gross Amount of Bill | {replacements['GROSS_AMOUNT']} |
| Less: Income Tax Deduction | {replacements['IT_DEDUCTION']} |
| Less: GST TDS | {replacements['GST_TDS']} |
| Less: Security Deposit | {replacements['SD_DEDUCTION']} |
| **Total Deductions** | **{replacements['TOTAL_DEDUCTIONS']}** |
| **Net Amount Payable** | **{replacements['NET_PAYABLE']}** |

The payment is sanctioned from the Budget Head: _______________

{designation or 'Sanctioning Authority'}
Name: {sanctioning_authority or '_______________'}
Date: {today_str()}
"""
        create_docx_from_text(content, output_path, "Sanction Letter")

    logger.info(f"Sanction Letter: {output_path}")
    return output_path


def update_account_of_work(
    case_id: str,
    case_name: str,
    bill_data: dict,
    previous_entries: list = None
) -> str:
    """Generate / update the Account of Work register entry. Returns output file path."""
    output_dir = get_case_folder(case_id, "Generated")
    filename = "Account_of_Work_Register.docx"
    output_path = str(output_dir / filename)

    prev_total = sum(e.get("net_paid", 0) for e in (previous_entries or []))
    this_bill_net = bill_data.get("net_payable", 0)
    cumulative = prev_total + this_bill_net

    prev_rows = ""
    for i, e in enumerate(previous_entries or [], 1):
        prev_rows += f"| {i} | {e.get('bill_no','—')} | {e.get('bill_date','—')} | Rs. {e.get('gross_amount',0):,.0f} | Rs. {e.get('net_paid',0):,.0f} |\n"
    new_row_no = len(previous_entries or []) + 1
    prev_rows += (
        f"| {new_row_no} | {bill_data.get('bill_no','—')} | {today_str()} | "
        f"Rs. {bill_data.get('gross_amount',0):,.0f} | Rs. {this_bill_net:,.0f} |\n"
    )

    content = f"""# ACCOUNT OF WORK REGISTER

**Name of Work:** {case_name}
**Work Order No.:** _______________
**Contractor:** {bill_data.get('contractor_name','_______________')}
**Contract Value:** Rs. _______________

---

## Bill-wise Payment Statement

| S.No. | Bill No. | Bill Date | Gross Amount | Net Paid |
|-------|----------|-----------|--------------|----------|
{prev_rows}
| | **TOTAL** | | | **Rs. {cumulative:,.0f}** |

**Balance Contract Value Remaining:** Rs. _______________

*Updated on: {today_str()}*
"""
    create_docx_from_text(content, output_path, "Account of Work Register")
    logger.info(f"Account of Work updated: {output_path}")
    return output_path


def process_bill(
    case_id: str,
    case_name: str,
    bill_pdf_path: str,
    work_order_no: str = "",
    contract_details: dict = None,
    officer_name: str = "",
    officer_designation: str = ""
) -> dict:
    """
    Full bill-processing pipeline: scrutinize → generate all 3 documents.
    Returns dict with status and paths to all generated documents.
    """
    # Step 1: Scrutinize
    bill_data = scrutinize_bill(bill_pdf_path, contract_details)

    # Step 2: Log in DB
    bill_id = add_bill({
        "case_id": case_id,
        "bill_no": bill_data.get("bill_no", ""),
        "bill_date": bill_data.get("bill_date", today_str()),
        "contractor_name": bill_data.get("contractor_name", ""),
        "gross_amount": bill_data.get("gross_amount", 0),
        "net_amount": bill_data.get("net_payable", 0),
        "deductions": bill_data.get("deductions", {}).get("total_deductions", 0),
        "status": bill_data.get("status", "Under Scrutiny"),
        "remarks": bill_data.get("remarks", "")
    })

    generated_docs = {}

    if bill_data.get("status") != "OBJECTION RAISED":
        # Generate documents only if bill is OK
        generated_docs["forwarding_letter"] = generate_bill_forwarding_letter(
            case_id, bill_data, case_name, work_order_no, officer_name, officer_designation)
        generated_docs["sanction_letter"] = generate_sanction_letter(
            case_id, bill_data, case_name, work_order_no, officer_name, officer_designation)
        generated_docs["account_of_work"] = update_account_of_work(
            case_id, case_name, bill_data)
    else:
        logger.warning(f"Bill has objections: {bill_data.get('discrepancies')}")

    return {
        "bill_id": bill_id,
        "bill_data": bill_data,
        "generated_docs": generated_docs
    }
