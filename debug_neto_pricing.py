#!/usr/bin/env python3
"""
Debug script - Shows all Neto API fields to identify the pricing field name
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

# Fetch 1 product with all fields to see pricing structure
payload = {
    "Filter": {
        "IsActive": "True",
        "Page": 0,
        "Limit": 1,
    }
}

print("Fetching 1 product from Neto API...")
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
        
        print("\n\nPRICING-RELATED FIELDS:")
        print("=" * 80)
        for key in sorted(product.keys()):
            if 'price' in key.lower() or 'cost' in key.lower() or 'rrp' in key.lower():
                value = product[key]
                print(f"{key}: {value}")
        
        print("\n\nFULL JSON (First 2000 chars):")
        print("=" * 80)
        print(json.dumps(product, indent=2)[:2000])
        
    else:
        print("No products returned")

except Exception as e:
    print(f"ERROR: {e}")
    print("\nMake sure these are set:")
    print("  - NETO_API_KEY")
    print("  - NETO_API_USERNAME")
