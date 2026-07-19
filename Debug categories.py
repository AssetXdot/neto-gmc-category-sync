#!/usr/bin/env python3
"""
Debug script to inspect the raw Neto API response for a product
specifically to see how Categories and Priority are structured
"""

import os
import requests
import json

NETO_API_KEY = os.getenv('NETO_API_KEY', '').strip()
NETO_API_USERNAME = os.getenv('NETO_API_USERNAME', '').strip()

if not NETO_API_KEY or not NETO_API_USERNAME:
    print("ERROR: NETO_API_KEY and NETO_API_USERNAME environment variables required")
    exit(1)

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

# Fetch the bar fridge product: 9351886001775
print("=" * 80)
print("FETCHING BAR FRIDGE PRODUCT: 9351886001775")
print("=" * 80)
print()

payload = {
    "Filter": {
        "SKU": "9351886001775",  # Use the product ID as SKU identifier
        "OutputSelector": ["SKU", "Name", "Categories"]
    }
}

try:
    response = neto_api_call("GetItem", payload)
    print("RAW API RESPONSE (full):")
    print(json.dumps(response, indent=2))
    print()
    print("=" * 80)
    print("CATEGORIES SECTION (extracted):")
    print("=" * 80)
    
    items = response.get("Item", [])
    if isinstance(items, dict):
        items = [items]
    
    for item in items:
        sku = item.get("SKU", "")
        name = item.get("Name", "")
        categories = item.get("Categories", [])
        
        print(f"\nProduct: {name} (SKU: {sku})")
        print(f"Categories structure type: {type(categories)}")
        print()
        print("Full Categories object:")
        print(json.dumps(categories, indent=2))
        print()
        
        # Try to parse different structures
        if isinstance(categories, dict):
            print("Categories is a DICT")
            cat_list = categories.get("Category", [])
            if isinstance(cat_list, dict):
                cat_list = [cat_list]
            print(f"  Number of categories: {len(cat_list)}")
            for idx, cat in enumerate(cat_list):
                print(f"  [{idx}] {cat}")
        
        elif isinstance(categories, list):
            print("Categories is a LIST")
            print(f"  Number of categories: {len(categories)}")
            for idx, cat in enumerate(categories):
                print(f"  [{idx}] {cat}")

except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)}")
    import traceback
    traceback.print_exc()
