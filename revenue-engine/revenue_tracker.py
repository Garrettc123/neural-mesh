"""
Quantum Revenue Engine - Multi-stream revenue tracking and optimization
Tracks, monitors, and optimizes revenue across all product lines
Author: Garrett Carroll
Date: December 2025
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict, field
from enum import Enum


class RevenueStream(Enum):
    """Revenue stream types"""
    SAAS_SUBSCRIPTION = "saas_subscription"
    USAGE_BASED = "usage_based"
    ENTERPRISE_LICENSE = "enterprise_license"
    MARKETPLACE = "marketplace"


@dataclass
class RevenueMetric:
    """Individual revenue metric"""
    stream_type: RevenueStream
    amount: float
    currency: str = "USD"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    source_product: str = ""


@dataclass
class ProductRevenue:
    """Product revenue tracking"""
    product_id: str
    product_name: str
    pricing_tier_min: float
    pricing_tier_max: float
    monthly_recurring_revenue: float
    active_customers: int = 0
    churn_rate: float = 0.0
    lifetime_value: float = 0.0
    revenue_history: List[RevenueMetric] = field(default_factory=list)


class QuantumRevenueEngine:
    """Advanced revenue tracking and optimization engine"""
    
    def __init__(self):
        self.products: Dict[str, ProductRevenue] = {}
        self.total_revenue_history: List[RevenueMetric] = []
        self.created_at = datetime.utcnow()
    
    def register_product(self, product_id: str, product_name: str, 
                        pricing_min: float, pricing_max: float,
                        target_mrr: float) -> ProductRevenue:
        """Register a new product in the revenue engine"""
        
        product = ProductRevenue(
            product_id=product_id,
            product_name=product_name,
            pricing_tier_min=pricing_min,
            pricing_tier_max=pricing_max,
            monthly_recurring_revenue=target_mrr
        )
        
        self.products[product_id] = product
        print(f\"✓ Registered: {product_name}\")\n        print(f\"  Target MRR: ${target_mrr:,.2f}\")\n        print(f\"  Annual Run Rate: ${target_mrr * 12:,.2f}\")\n        \n        return product\n    \n    def record_transaction(self, product_id: str, amount: float,\n                          stream_type: RevenueStream = RevenueStream.SAAS_SUBSCRIPTION) -> bool:\n        \"\"\"Record a revenue transaction\"\"\"\n        \n        if product_id not in self.products:\n            return False\n        \n        metric = RevenueMetric(\n            stream_type=stream_type,\n            amount=amount,\n            source_product=product_id\n        )\n        \n        self.products[product_id].revenue_history.append(metric)\n        self.total_revenue_history.append(metric)\n        \n        return True\n    \n    def update_customer_count(self, product_id: str, customer_count: int) -> bool:\n        \"\"\"Update active customer count for a product\"\"\"\n        \n        if product_id not in self.products:\n            return False\n        \n        self.products[product_id].active_customers = customer_count\n        return True\n    \n    def calculate_churn_rate(self, product_id: str, previous_customers: int,\n                            current_customers: int, period_days: int = 30) -> float:\n        \"\"\"Calculate churn rate for a product\"\"\"\n        \n        if previous_customers == 0:\n            return 0.0\n        \n        churned = max(0, previous_customers - current_customers)\n        churn_rate = (churned / previous_customers) * 100\n        \n        if product_id in self.products:\n            self.products[product_id].churn_rate = churn_rate\n        \n        return churn_rate\n    \n    def calculate_ltv(self, product_id: str, avg_monthly_revenue: float,\n                     avg_customer_lifespan_months: int = 24) -> float:\n        \"\"\"Calculate lifetime value for a product\"\"\"\n        \n        ltv = avg_monthly_revenue * avg_customer_lifespan_months\n        \n        if product_id in self.products:\n            self.products[product_id].lifetime_value = ltv\n        \n        return ltv\n    \n    def get_product_summary(self, product_id: str) -> Optional[Dict]:\n        \"\"\"Get revenue summary for a specific product\"\"\"\n        \n        if product_id not in self.products:\n            return None\n        \n        product = self.products[product_id]\n        total_transactions = sum(m.amount for m in product.revenue_history)\n        \n        return {\n            \"product_id\": product.product_id,\n            \"product_name\": product.product_name,\n            \"target_mrr\": product.monthly_recurring_revenue,\n            \"annual_run_rate\": product.monthly_recurring_revenue * 12,\n            \"active_customers\": product.active_customers,\n            \"churn_rate\": f\"{product.churn_rate:.2f}%\",\n            \"lifetime_value\": f\"${product.lifetime_value:,.2f}\",\n            \"total_recorded_revenue\": f\"${total_transactions:,.2f}\",\n            \"pricing_range\": f\"${product.pricing_tier_min} - ${product.pricing_tier_max}/month\"\n        }\n    \n    def get_portfolio_summary(self) -> Dict:\n        \"\"\"Get summary of entire revenue portfolio\"\"\"\n        \n        total_mrr = sum(p.monthly_recurring_revenue for p in self.products.values())\n        total_arr = total_mrr * 12\n        total_customers = sum(p.active_customers for p in self.products.values())\n        avg_churn = sum(p.churn_rate for p in self.products.values()) / len(self.products) if self.products else 0\n        \n        return {\n            \"total_products\": len(self.products),\n            \"total_mrr\": f\"${total_mrr:,.2f}\",\n            \"total_arr\": f\"${total_arr:,.2f}\",\n            \"total_active_customers\": total_customers,\n            \"average_churn_rate\": f\"{avg_churn:.2f}%\",\n            \"portfolio\": [self.get_product_summary(pid) for pid in self.products.keys()]\n        }\n    \n    def project_revenue(self, months: int = 12) -> Dict:\n        \"\"\"Project revenue for next N months\"\"\"\n        \n        projections = []\n        current_date = datetime.utcnow()\n        \n        for month in range(1, months + 1):\n            projection_date = current_date + timedelta(days=30 * month)\n            \n            # Calculate projection considering churn\n            total_projected = 0\n            for product in self.products.values():\n                # Simple projection: MRR adjusted for churn\n                churn_factor = 1 - (product.churn_rate / 100)\n                projected_mrr = product.monthly_recurring_revenue * (churn_factor ** month)\n                total_projected += projected_mrr\n            \n            projections.append({\n                \"month\": month,\n                \"date\": projection_date.isoformat(),\n                \"projected_mrr\": f\"${total_projected:,.2f}\",\n                \"projected_arr\": f\"${total_projected * 12:,.2f}\"\n            })\n        \n        return {\n            \"projection_period_months\": months,\n            \"projections\": projections\n        }\n    \n    def export_report(self, filepath: str) -> bool:\n        \"\"\"Export revenue report to JSON file\"\"\"\n        \n        try:\n            report = {\n                \"generated_at\": datetime.utcnow().isoformat(),\n                \"portfolio_summary\": self.get_portfolio_summary(),\n                \"revenue_projections\": self.project_revenue(12),\n                \"products\": {\n                    pid: asdict(product) for pid, product in self.products.items()\n                }\n            }\n            \n            # Convert lists to JSON-serializable format\n            report_json = json.dumps(report, indent=2, default=str)\n            \n            with open(filepath, 'w') as f:\n                f.write(report_json)\n            \n            return True\n        except Exception as e:\n            print(f\"✗ Error exporting report: {str(e)}\")\n            return False\n\n\nif __name__ == \"__main__\":\n    # Example usage\n    engine = QuantumRevenueEngine()\n    \n    # Register products\n    engine.register_product(\n        \"mesh-messenger\",\n        \"Zero-Human Mesh Messenger\",\n        9.0, 99.0, 14500.0\n    )\n    \n    engine.register_product(\n        \"governance-platform\",\n        \"Zero-Human AI Governance Platform\",\n        299.0, 1999.0, 50000.0\n    )\n    \n    # Update metrics\n    engine.update_customer_count(\"mesh-messenger\", 2000)\n    engine.update_customer_count(\"governance-platform\", 50)\n    \n    # Calculate churn\n    engine.calculate_churn_rate(\"mesh-messenger\", 2100, 2000)\n    engine.calculate_churn_rate(\"governance-platform\", 52, 50)\n    \n    # Calculate LTV\n    engine.calculate_ltv(\"mesh-messenger\", 72.50, 24)  # $7.25/customer/month\n    engine.calculate_ltv(\"governance-platform\", 1000, 24)\n    \n    # Print portfolio summary\n    print(\"\\n\" + \"=\"*60)\n    print(\"PORTFOLIO REVENUE SUMMARY\")\n    print(\"=\"*60)\n    \n    summary = engine.get_portfolio_summary()\n    print(f\"Total Products: {summary['total_products']}\")\n    print(f\"Total MRR: {summary['total_mrr']}\")\n    print(f\"Total ARR: {summary['total_arr']}\")\n    print(f\"Total Active Customers: {summary['total_active_customers']}\")\n    print(f\"Average Churn Rate: {summary['average_churn_rate']}\")\n    \n    # Export report\n    engine.export_report(\"revenue_report.json\")\n    print(f\"\\n✓ Report exported to revenue_report.json\")\n