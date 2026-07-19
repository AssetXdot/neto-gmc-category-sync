#!/usr/bin/env python3
"""
Debug script - Verify pricing and custom field access from Neto API
Uses correct API field names from Neto documentation
"""

import os
import json
import requests

NETO_API_KEY = os.getenv('NETO_API_KEY', '').strip()
NETO_API_USERNAME = os.getenv('NETO_API_USERNAME', '').strip()

def neto_api_call(action: str, payload: dict) -> dict:
    """Make authenticated call to Neto API"""
    url = "https://www.thebbqstore.com.au/do/WS/NetoAPI"
    
    headers = {
        'NETOAPI_ACTION': action,
        'NETOAPI_USERNAME': NETO_API_USERNAME,
        'NETOAPI_KEY': NETO_API_KEY,
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }
    
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()

# Fetch 1 product with pricing and custom fields
payload = {
    "Filter": {
        "IsActive": "True",
        "OutputSelector": ["SKU", "Name", "RRP", "DefaultPrice", "Misc02"],
        "Page": 0,
        "Limit": 1,
    }
}

print("Fetching 1 product from Neto API with pricing fields...")
print("=" * 80)

try:
    data = neto_api_call("GetItem", payload)
    
    items = data.get("Item", [])
    if isinstance(items, dict):
        items = [items]
    
    if items:
        product = items[0]
        
        print("\nALL FIELDS IN PRODUCT:")
        print("=" * 80)
        for key in sorted(product.keys()):
            value = product[key]
            print(f"{key}: {str(value)[:150]}")
        
        print("\n\nPRICING & CUSTOM FIELDS:")
        print("=" * 80)
        print(f"RRP (Recommended Retail Price): {product.get('RRP', 'NOT FOUND')}")
        print(f"DefaultPrice (Website Price): {product.get('DefaultPrice', 'NOT FOUND')}")
        print(f"Misc02 (PriceSpy): {product.get('Misc02', 'NOT FOUND')}")
        
        print("\n\nFULL JSON:")
        print("=" * 80)
        print(json.dumps(product, indent=2))
        
    else:
        print("No products returned")

except Exception as e:
    print(f"ERROR: {e}")
    print("\nMake sure these are set:")
    print("  - NETO_API_KEY")
    print("  - NETO_API_USERNAME")
