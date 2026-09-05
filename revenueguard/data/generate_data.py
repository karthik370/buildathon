"""
Data generation / loading script for RevenueGuard.

DATA REALISM NOTE
---
The CSV data used by this project is grounded in real published data sources:

1. Razorpay's official error code list:
   https://razorpay.com/docs/errors/payments/list/
   Every failure_code value comes from this list.  failure_source classification
   (customer vs gateway) matches Razorpay's published categorisation.

2. NPCI's published decline-type split:
   Business Decline (customer-side: insufficient funds, wrong PIN, etc.) ≈ 80%
   Technical Decline (bank/network-side: timeout, unavailable, etc.)    ≈ 20%
   The failure_code sampling weights are calibrated to this real ratio.

3. NPCI's published auto-debit / mandate failure data:
   Real-world recurring-payment (subscription) failure rates run ~30-45%.
   Subscription renewal failure rate in the data is ~32.5% (12/40), matching
   this benchmark.

DOCUMENTED DEMO-SCALE ASSUMPTION:
   Overall payment failure volume (~22% for normal customers, ~55% for repeat
   failers) is intentionally elevated above the real aggregate NPCI decline rate
   (~9-10%) to ensure the demo batch has enough at-risk cases.  The *relative
   skew* across failure types IS calibrated to real published ratios.

The pre-generated CSVs include two gap-fix columns:
  - is_disputed on payment_transactions (2 rows guaranteed True)
  - promise_to_pay_date / promise_to_pay_status on invoices
"""

import csv
import os
from pathlib import Path
from collections import Counter
from datetime import datetime

DATA_DIR = Path(__file__).parent


def load_csv(filename: str) -> list[dict]:
    """Load a CSV file from the data directory and return as list of dicts."""
    filepath = DATA_DIR / filename
    with open(filepath, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def print_data_summary():
    """Print summary statistics of the synthetic data for verification."""
    customers = load_csv("customers.csv")
    payments = load_csv("payment_transactions.csv")
    checkouts = load_csv("checkout_sessions.csv")
    subscriptions = load_csv("subscriptions.csv")
    invoices = load_csv("invoices.csv")

    print("=" * 70)
    print("REVENUEGUARD — DATA SUMMARY")
    print("=" * 70)

    # Customers
    repeat_failers = sum(1 for c in customers if c["is_repeat_failer"] == "True")
    print(f"\nCustomers: {len(customers)} total, {repeat_failers} repeat failers "
          f"({repeat_failers / len(customers) * 100:.1f}%)")

    # Payment transactions
    failed_payments = [p for p in payments if p["status"] == "failed"]
    success_payments = [p for p in payments if p["status"] == "success"]
    disputed = [p for p in payments if p.get("is_disputed") == "True"]
    failed_amount = sum(float(p["amount"]) for p in failed_payments)
    print(f"\nPayment Transactions: {len(payments)} total")
    print(f"  Successes: {len(success_payments)}")
    print(f"  Failures:  {len(failed_payments)} ({len(failed_payments)/len(payments)*100:.1f}%)")
    print(f"  Disputed:  {len(disputed)}")
    print(f"  Amount at risk (failed): Rs.{failed_amount:,.2f}")

    # Failure code distribution
    code_counts = Counter(p["failure_code"] for p in failed_payments)
    source_counts = Counter(p["failure_source"] for p in failed_payments)
    category_counts = Counter(p["failure_category"] for p in failed_payments)
    print(f"\n  Failure code distribution:")
    for code, count in code_counts.most_common():
        src = next(p["failure_source"] for p in failed_payments if p["failure_code"] == code)
        cat = next(p["failure_category"] for p in failed_payments if p["failure_code"] == code)
        print(f"    {code:<25} {count:>3} ({count/len(failed_payments)*100:5.1f}%)  [{src}/{cat}]")
    print(f"\n  Business decline: {category_counts.get('business_decline', 0)} "
          f"({category_counts.get('business_decline', 0)/len(failed_payments)*100:.0f}%)")
    print(f"  Technical decline: {category_counts.get('technical_decline', 0)} "
          f"({category_counts.get('technical_decline', 0)/len(failed_payments)*100:.0f}%)")
    print(f"  (Target: ~80/20 per NPCI published data)")

    # Payment method distribution
    method_counts = Counter(p["payment_method"] for p in payments)
    print(f"\n  Payment method distribution:")
    for method, count in method_counts.most_common():
        print(f"    {method:<12} {count:>3} ({count/len(payments)*100:.1f}%)")

    # Checkout sessions
    abandoned = [s for s in checkouts if s["status"] == "abandoned"]
    abandoned_amount = sum(float(s["cart_value"]) for s in abandoned)
    print(f"\nCheckout Sessions: {len(checkouts)} total")
    print(f"  Abandoned: {len(abandoned)} ({len(abandoned)/len(checkouts)*100:.1f}%)")
    print(f"  Amount at risk (abandoned carts): Rs.{abandoned_amount:,.2f}")

    # Subscriptions
    failed_renewals = [s for s in subscriptions if s["last_renewal_status"] == "failed"]
    renewal_amount = sum(float(s["monthly_amount"]) for s in failed_renewals)
    print(f"\nSubscriptions: {len(subscriptions)} total (all status=active)")
    print(f"  Failed renewals: {len(failed_renewals)} "
          f"({len(failed_renewals)/len(subscriptions)*100:.1f}%)")
    print(f"  (NPCI auto-debit benchmark: ~30-45%)")
    print(f"  Amount at risk (failed renewals): Rs.{renewal_amount:,.2f}")

    # Invoices
    now = datetime.now()
    unpaid = [i for i in invoices if i["status"] == "unpaid"]
    overdue = [i for i in unpaid if datetime.fromisoformat(i["due_date"]) < now]
    overdue_amount = sum(float(i["amount"]) for i in overdue)
    unpaid_amount = sum(float(i["amount"]) for i in unpaid)
    promise_rows = [i for i in invoices if i.get("promise_to_pay_date")]
    print(f"\nInvoices: {len(invoices)} total")
    print(f"  Unpaid: {len(unpaid)} ({len(unpaid)/len(invoices)*100:.1f}%)")
    print(f"  Overdue: {len(overdue)}")
    print(f"  With promise-to-pay: {len(promise_rows)}")
    print(f"  Amount at risk (unpaid): Rs.{unpaid_amount:,.2f}")
    print(f"  Amount at risk (overdue): Rs.{overdue_amount:,.2f}")

    # Combined
    total_at_risk = failed_amount + abandoned_amount + renewal_amount + unpaid_amount
    print(f"\n{'-'*70}")
    print(f"TOTAL COMBINED AMOUNT AT RISK: Rs.{total_at_risk:,.2f}")
    print(f"  Payments:      Rs.{failed_amount:>12,.2f}")
    print(f"  Checkouts:     Rs.{abandoned_amount:>12,.2f}")
    print(f"  Subscriptions: Rs.{renewal_amount:>12,.2f}")
    print(f"  Invoices:      Rs.{unpaid_amount:>12,.2f}")
    print("=" * 70)


if __name__ == "__main__":
    print_data_summary()
