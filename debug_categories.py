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

# Search for products with "Bar Fridge" in name
print("=" * 80)
print("SEARCHING FOR PRODUCTS WITH 'BAR FRIDGE' IN NAME")
print("=" * 80)
print()

payload = {
    "Filter": {
        "IsActive": "True",
        "Limit": 200,
        "OutputSelector": ["SKU", "Name", "Categories"]
    }
}

try:
    response = neto_api_call("GetItem", payload)
    items = response.get("Item", [])
    
    if isinstance(items, dict):
        items = [items]
    
    # Find products with Bar Fridge in name or categories
    bar_fridge_products = []
    
    for item in items:
        name = item.get("Name", "").lower()
        if "bar fridge" in name or "bar-fridge" in name:
            bar_fridge_products.append(item)
    
    print(f"Found {len(bar_fridge_products)} products with 'Bar Fridge' in name")
    print()
    
    if bar_fridge_products:
        # Show details of first bar fridge product
        product = bar_fridge_products[0]
        sku = product.get("SKU", "")
        name = product.get("Name", "")
        categories = product.get("Categories", [])
        
        print("=" * 80)
        print(f"PRODUCT: {name}")
        print(f"SKU: {sku}")
        print("=" * 80)
        print()
        print("RAW API RESPONSE for this product:")
        print(json.dumps(product, indent=2))
        print()
        print("=" * 80)
        print("CATEGORIES SECTION (extracted):")
        print("=" * 80)
        print(json.dumps(categories, indent=2))
        print()
        print("=" * 80)
        print("DETAILED ANALYSIS:")
        print("=" * 80)
        
        if isinstance(categories, dict):
            print("Categories is a DICT")
            cat_list = categories.get("Category", [])
            if isinstance(cat_list, dict):
                cat_list = [cat_list]
            print(f"Number of category paths: {len(cat_list)}")
            print()
            for idx, cat in enumerate(cat_list):
                print(f"Category Path {idx + 1}:")
                print(f"  {json.dumps(cat, indent=4)}")
                print()
        
        elif isinstance(categories, list):
            print("Categories is a LIST")
            print(f"Number of categories: {len(categories)}")
            for idx, cat in enumerate(categories):
                print(f"Category {idx + 1}: {json.dumps(cat, indent=2)}")
    else:
        print("ERROR: No products found with 'Bar Fridge' in name")
        print("Showing first product as example...")
        print()
        if items:
            product = items[0]
            print(json.dumps(product, indent=2))

except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)}")
    import traceback
    traceback.print_exc()
