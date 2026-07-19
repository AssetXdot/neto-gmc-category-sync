#!/usr/bin/env python3
"""
Neto to Google Merchant Center Category Feed
Extracts multiple categories from Neto products and generates supplementary feed with images
Intelligent image caching - only re-verifies changed products
Git commit/push is handled by GitHub Actions workflow
"""

import os
import json
import time
import requests
from datetime import datetime
from typing import List, Dict, Any
import logging
import xml.etree.ElementTree as ET
import re
import hashlib

# ============================================================================
# SECURE LOGGING - Masks credentials in all log output
# ============================================================================

class SecureFormatter(logging.Formatter):
    """Custom formatter that masks sensitive data in logs"""
    
    def format(self, record):
        msg = str(record.msg)
        if record.args:
            try:
                msg = msg % record.args
            except (TypeError, ValueError):
                pass
        
        # Mask API keys, tokens, and credentials
        msg = re.sub(r'(NETOAPI_KEY|NETO_API_KEY)[:\s]*[^\s]+', r'\1=***MASKED***', msg, flags=re.IGNORECASE)
        msg = re.sub(r'(NETOAPI_USERNAME|API_USERNAME)[:\s]*[^\s]+', r'\1=***MASKED***', msg, flags=re.IGNORECASE)
        msg = re.sub(r'(Authorization|Bearer)[:\s]*[^\s]+', r'\1: ***MASKED***', msg, flags=re.IGNORECASE)
        msg = re.sub(r'(password|secret|token)[=:\s]*[^\s]+', r'\1=***MASKED***', msg, flags=re.IGNORECASE)
        msg = re.sub(r'"client_email":\s*"[^"]*"', r'"client_email": "***MASKED***"', msg)
        msg = re.sub(r'"private_key":\s*"[^"]*"', r'"private_key": "***MASKED***"', msg)
        
        record.msg = msg
        record.args = ()
        return super().format(record)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
for handler in logger.handlers:
    handler.setFormatter(SecureFormatter('%(asctime)s - %(levelname)s - %(message)s'))

# ============================================================================
# CONFIGURATION - Read from environment variables
# ============================================================================

NETO_API_KEY = os.getenv('NETO_API_KEY', '').strip()
NETO_API_USERNAME = os.getenv('NETO_API_USERNAME', '').strip()
GOOGLE_MERCHANT_ID = os.getenv('GOOGLE_MERCHANT_ID', '').strip()
GOOGLE_CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON', '').strip()
DATAFEEDWATCH_URL = os.getenv('DATAFEEDWATCH_URL', '').strip()

# Image cache file
IMAGE_CACHE_FILE = "image_cache.json"

# Validate environment variables
if not NETO_API_KEY:
    raise ValueError("NETO_API_KEY not set in environment variables")
if not NETO_API_USERNAME:
    raise ValueError("NETO_API_USERNAME not set in environment variables")
if not GOOGLE_CREDENTIALS_JSON:
    raise ValueError("GOOGLE_CREDENTIALS_JSON not set in environment variables")
if not DATAFEEDWATCH_URL:
    raise ValueError("DATAFEEDWATCH_URL not set in environment variables")
if not GOOGLE_MERCHANT_ID:
    raise ValueError("GOOGLE_MERCHANT_ID not set in environment variables")

# Parse Google credentials
try:
    GOOGLE_CREDS = json.loads(GOOGLE_CREDENTIALS_JSON)
except json.JSONDecodeError as e:
    logger.error("GOOGLE_CREDENTIALS_JSON is not valid JSON")
    raise ValueError("Invalid GOOGLE_CREDENTIALS_JSON")

# ============================================================================
# IMAGE CACHE FUNCTIONS
# ============================================================================

def load_image_cache() -> Dict[str, Dict[str, Any]]:
    """Not used - images removed from feed"""
    return {}

def save_image_cache(cache: Dict[str, Dict[str, Any]]) -> bool:
    """Not used - images removed from feed"""
    return True

def get_product_hash(product: Dict[str, Any]) -> str:
    """Not used - images removed from feed"""
    return ""

# ============================================================================
# DATAFEEDWATCH FEED FUNCTIONS
# ============================================================================

def fetch_datafeedwatch_products() -> Dict[str, Dict[str, Any]]:
    """
    Fetch datafeedwatch feed and extract product ID mappings.
    Creates mappings for both GTIN and SKU to handle different matching scenarios.
    Returns: {"GTIN_or_SKU": {"id": "g:id_value", "gtin": "...", "sku": "..."}, ...}
    """
    logger.info("Fetching datafeedwatch feed...")
    
    try:
        response = requests.get(DATAFEEDWATCH_URL, timeout=30)
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        products = {}
        sku_count = 0
        gtin_count = 0
        
        # Parse XML feed - namespace for Google Shopping
        ns = {'g': 'http://base.google.com/ns/1.0'}
        
        for item in root.findall('.//item'):
            # Get product ID (g:id)
            product_id_elem = item.find('g:id', ns)
            if product_id_elem is None or not product_id_elem.text:
                continue
            
            product_id = product_id_elem.text.strip()
            if not product_id:
                continue
            
            # Extract GTIN
            gtin_elem = item.find('g:gtin', ns)
            gtin = gtin_elem.text.strip() if gtin_elem is not None and gtin_elem.text else None
            
            # Extract SKU (both g:SKU and variations)
            sku_elem = item.find('g:SKU', ns)
            if sku_elem is None or not sku_elem.text:
                sku_elem = item.find('{http://base.google.com/ns/1.0}SKU', ns)
            sku = sku_elem.text.strip() if sku_elem is not None and sku_elem.text else None
            
            # Create entry with both GTIN and SKU
            entry = {'id': product_id}
            if gtin:
                entry['gtin'] = gtin
                products[gtin] = entry
                gtin_count += 1
            if sku:
                entry['sku'] = sku
                products[sku] = entry
                sku_count += 1
                logger.debug(f"Datafeedwatch: Loaded SKU={sku}, GTIN={gtin}, ID={product_id}")
            
            products[product_id] = entry
        
        logger.info(f"Datafeedwatch products loaded: {len(products)} total mappings")
        logger.info(f"  SKU mappings: {sku_count}")
        logger.info(f"  GTIN mappings: {gtin_count}")
        logger.info(f"  Sample keys in products dict: {list(products.keys())[:10]}")
        
        return products
    
    except Exception as e:
        logger.error(f"Error fetching datafeedwatch feed: {type(e).__name__}")
        return {}

# ============================================================================
# CATEGORY NORMALIZATION FUNCTIONS
# ============================================================================

def normalize_category_name(category: str) -> str:
    """
    Normalize category names to a standard format.
    Converts singular forms to plural and standardizes capitalization.
    """
    if not category:
        return category
    
    # Mapping of singular to plural forms (case-insensitive)
    singular_to_plural = {
        'smoker': 'smokers',
        'grill': 'grills',
        'bbq': 'bbqs',
        'oven': 'ovens',
        'heater': 'heaters',
        'cover': 'covers',
        'accessory': 'accessories',
        'burner': 'burners',
        'rotisserie': 'rotisseries',
        'grate': 'grates',
        'thermometer': 'thermometers',
        'light': 'lights',
        'basket': 'baskets',
        'rack': 'racks',
        'tool': 'tools',
        'brush': 'brushes',
        'cleaner': 'cleaners',
        'mat': 'mats',
        'apron': 'aprons',
    }
    
    # Normalize: check if category ends with a singular form and convert to plural
    category_lower = category.lower().strip()
    original_category = category
    
    for singular, plural in singular_to_plural.items():
        # Check if category ends with singular form (word boundary)
        if category_lower.endswith(' ' + singular) or category_lower == singular:
            # Replace the singular form with plural
            if category_lower == singular:
                normalized = plural
            else:
                # Replace the last word if it's singular
                normalized = category[:-len(singular)] + plural
            
            logger.debug(f"Normalized category: '{original_category}' → '{normalized}'")
            return normalized
    
    # If no singular form found, return as-is
    return category

def normalize_category_list(categories: List[str]) -> List[str]:
    """Normalize a list of categories"""
    return [normalize_category_name(cat) for cat in categories]

def neto_api_call(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Make authenticated call to Neto API"""
    url = "https://www.thebbqstore.com.au/do/WS/NetoAPI"
    
    headers = {
        'NETOAPI_ACTION': action,
        'NETOAPI_USERNAME': NETO_API_USERNAME,
        'NETOAPI_KEY': NETO_API_KEY,
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }
    
    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        
        # Check for Neto API errors
        if isinstance(data, dict) and 'Error' in data:
            error_msg = data['Error'].get('Message', 'Unknown error')
            raise ValueError(f"Neto API error: {error_msg}")
        
        return data
    except requests.exceptions.RequestException as e:
        logger.error(f"Neto API error: {type(e).__name__}")
        raise

def extract_categories(item: Dict[str, Any], normalize: bool = True) -> List[str]:
    """
    Extract category names from Neto's nested Categories structure.
    
    Structure: [{"Category": [{"CategoryName": "X"}, {"CategoryName": "Y"}]}]
    
    Returns: ["Category1", "Category2", ...]
    """
    categories = []
    cats_raw = item.get("Categories", [])
    sku = item.get("SKU", "")
    
    logger.debug(f"SKU {sku}: Raw categories type: {type(cats_raw)}, value: {str(cats_raw)[:200]}")
    
    # Normalize to list
    if isinstance(cats_raw, dict):
        cats_raw = [cats_raw]
    
    # Extract CategoryName from each category
    for wrapper in cats_raw:
        if not isinstance(wrapper, dict):
            logger.debug(f"SKU {sku}: Wrapper is not dict, skipping: {type(wrapper)}")
            continue
        
        cat_list = wrapper.get("Category", [])
        if isinstance(cat_list, dict):
            cat_list = [cat_list]
        
        logger.debug(f"SKU {sku}: Found {len(cat_list)} categories in wrapper")
        
        for cat in cat_list:
            if isinstance(cat, dict):
                name = cat.get("CategoryName", "").strip()
                if name:
                    categories.append(name)
                    logger.debug(f"SKU {sku}: Extracted category: {name}")
    
    logger.debug(f"SKU {sku}: Total extracted categories: {len(categories)} → {categories}")
    
    # Apply normalization to standardize singular/plural forms
    if normalize:
        categories = normalize_category_list(categories)
        logger.debug(f"SKU {sku}: After normalization: {categories}")
    
    return categories

# REMOVED: No more HEAD request checking - too slow and gets blocked
# Just build URLs for all formats in order, GMC will validate them

def build_product_images(sku: str, product: Dict[str, Any], cache: Dict[str, Dict[str, Any]]) -> List[str]:
    """
    NOT USED - Images removed from feed
    Kept as stub to avoid errors
    """
    return []

def extract_product_ids(item: Dict[str, Any]) -> Dict[str, str]:
    """
    Extract product identifiers from Neto item.
    Returns: {"sku": "...", "upc": "...", "gtin": "..."}
    """
    result = {}
    sku = item.get("SKU", "").strip()
    if sku:
        result['sku'] = sku
    
    logger.debug(f"Processing product")
    
    # Try to get UPC/GTIN/Barcode from various field names
    for field in ["UPC", "GTIN", "Barcode", "EAN", "ProductBarcode", "Ean"]:
        value = item.get(field, "").strip()
        if value:
            result['upc'] = value
            logger.debug(f"Found UPC")
            break
    
    if 'upc' not in result:
        logger.debug(f"No UPC found for SKU {sku}")
    
    return result

def fetch_all_products() -> List[Dict[str, Any]]:
    """Fetch all active products from Neto with categories"""
    logger.info("Fetching products from Neto...")
    
    all_items = []
    page = 0
    
    while True:
        try:
            payload = {
                "Filter": {
                    "IsActive": "True",
                    "OutputSelector": [
                        "SKU", "Name", "Brand", "DefaultPrice", "Categories", "RRP", "Misc02"
                    ],
                    "Page": page,
                    "Limit": 200,
                }
            }
            
            data = neto_api_call("GetItem", payload)
            
            items = data.get("Item", [])
            
            if isinstance(items, dict):
                items = [items]
            
            if not items:
                break
            
            all_items.extend(items)
            logger.info(f"  Page {page}: {len(items)} items (total: {len(all_items)})")
            page += 1
            time.sleep(0.3)  # Rate limiting
            
        except Exception as e:
            logger.error(f"Error fetching page {page}: {type(e).__name__}")
            break
    
    logger.info(f"Total products fetched: {len(all_items)}")
    return all_items

def build_category_feed(products: List[Dict[str, Any]], datafeedwatch_products: Dict[str, Dict[str, Any]], cache: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build feed entries matching Neto products with datafeedwatch product IDs.
    
    Includes discounted pricing ONLY if:
    - PriceSpy field is NOT TRUE (meaning PriceSpy is NOT managing the price)
    - Website price < RRP (product is discounted)
    
    Format: [{"id": "datafeedwatch_id", "product_type": "...", "availability": "...", "images": [...], "sale_price": ...}, ...]
    """
    feed_entries = []
    products_with_categories = 0
    products_without_categories = 0
    products_not_in_datafeedwatch = 0
    matched_skus = []
    unmatched_skus = []
    unmatched_details = []
    products_with_sale_price = 0
    
    for product in products:
        sku = product.get("SKU", "").strip()
        if not sku:
            continue
        
        # DEBUG: Ultra-verbose logging for problem SKUs + pizza ovens
        is_debug_sku = sku in ['THX4000DC', 'TREK2.0', 'TFT18KLDA']
        is_pizza_product = 'pizza' in product.get('Name', '').lower()
        if is_debug_sku or is_pizza_product:
            logger.info(f"\n*** DEBUG SKU {sku} ***")
            logger.info(f"  Name: {product.get('Name', '')[:100]}")
            logger.info(f"  Raw product keys: {list(product.keys())}")
        
        # Extract categories from Neto's nested structure
        categories = extract_categories(product, normalize=True)
        
        if is_debug_sku or is_pizza_product:
            logger.info(f"  Extracted categories: {categories}")
        
        if not categories:
            products_without_categories += 1
            unmatched_skus.append(sku)
            unmatched_details.append(f"{sku} (no categories)")
            if is_debug_sku or is_pizza_product:
                logger.info(f"  RESULT: SKIPPED - No categories found")
            logger.debug(f"SKU {sku}: SKIPPED - No categories found")
            continue
        
        products_with_categories += 1
        logger.debug(f"SKU {sku}: Has {len(categories)} categories: {categories}")
        
        # Try to find in datafeedwatch by SKU, GTIN, or product ID
        datafeedwatch_id = None
        product_ids = extract_product_ids(product)
        
        # Try SKU first
        if sku in datafeedwatch_products:
            datafeedwatch_id = datafeedwatch_products[sku].get('id')
            matched_skus.append(sku)
            logger.debug(f"SKU {sku}: ✓ MATCHED in datafeedwatch (by SKU)")
        # Try UPC
        elif 'upc' in product_ids and product_ids['upc'] in datafeedwatch_products:
            datafeedwatch_id = datafeedwatch_products[product_ids['upc']].get('id')
            matched_skus.append(sku)
            logger.debug(f"SKU {sku}: ✓ MATCHED in datafeedwatch (by UPC: {product_ids['upc']})")
        
        if not datafeedwatch_id:
            products_not_in_datafeedwatch += 1
            unmatched_skus.append(sku)
            unmatched_details.append(f"{sku} (not in datafeedwatch)")
            if is_debug_sku or is_pizza_product:
                logger.info(f"  RESULT: NOT FOUND in datafeedwatch")
                logger.info(f"  Product IDs searched: {product_ids}")
            logger.debug(f"SKU {sku}: ✗ NOT FOUND in datafeedwatch - Skipping")
            logger.debug(f"  Product IDs searched: {product_ids}")
            logger.debug(f"  Categories found: {categories}")
            continue
        
        if is_debug_sku or is_pizza_product:
            logger.info(f"  ✓ MATCHED in datafeedwatch: {datafeedwatch_id}")
        
        # Build product type string from categories
        product_type = " > ".join(categories)
        
        if is_debug_sku or is_pizza_product:
            logger.info(f"  Product type: {product_type}")
        
        # Determine availability
        availability = "in stock"  # Default
        
        # Check pricing - only add sale price if PriceSpy is NOT managing it AND price is discounted
        sale_price = None
        priceSpy_field = product.get("Misc02", "").strip().upper()  # Misc02 is the API name for MISC2
        
        if is_debug_sku or is_pizza_product:
            logger.info(f"  Misc02/PriceSpy: {priceSpy_field}")
            logger.info(f"  RRP: {product.get('RRP')}, DefaultPrice: {product.get('DefaultPrice')}")
        
        if priceSpy_field != "TRUE":  # PriceSpy is NOT managing the price
            # Get RRP (retail/recommended price) and DefaultPrice (website selling price)
            rrp = product.get("RRP")
            website_price = product.get("DefaultPrice")
            
            # Check if there's a discount (website price < RRP)
            if website_price and rrp:
                try:
                    website_price_float = float(website_price)
                    rrp_float = float(rrp)
                    # Only include sale price if website price is less than RRP
                    if website_price_float < rrp_float:
                        sale_price = website_price_float
                        products_with_sale_price += 1
                        if is_debug_sku or is_pizza_product:
                            logger.info(f"  Sale price included: {sale_price}")
                        logger.debug(f"SKU {sku}: Sale price included (PriceSpy not managing, discounted: ${rrp_float} → ${website_price_float})")
                except (ValueError, TypeError):
                    pass
        
        if is_debug_sku or is_pizza_product:
            logger.info(f"  ADDING TO FEED")
        
        feed_entries.append({
            "id": datafeedwatch_id,
            "product_type": product_type,
            "availability": availability,
            "sale_price": sale_price
        })
    
    # Log debug info
    logger.info(f"Products with categories: {products_with_categories}")
    logger.info(f"Products without categories: {products_without_categories}")
    logger.info(f"Products not in datafeedwatch: {products_not_in_datafeedwatch}")
    logger.info(f"Total feed entries: {len(feed_entries)}")
    logger.info(f"Products with sale price: {products_with_sale_price} (PriceSpy not managing & discounted)")
    logger.info(f"")
    logger.info(f"Sample matched SKUs: {matched_skus[:5]}")
    logger.info(f"Sample unmatched (first 5): {unmatched_details[:5]}")
    logger.info(f"Total unmatched: {len(unmatched_skus)}")
    logger.info(f"Datafeedwatch keys (sample): {list(datafeedwatch_products.keys())[:20]}")
    logger.info(f"Total datafeedwatch keys: {len(datafeedwatch_products)}")
    
    # DEBUG: Check specific problem SKUs
    problem_skus = ['THX4000DC', 'TREK2.0', 'TFT18KLDA']
    logger.info(f"\n=== DEBUG: Checking problem SKUs ===")
    for problem_sku in problem_skus:
        in_datafeedwatch = problem_sku in datafeedwatch_products
        in_unmatched = problem_sku in unmatched_skus
        in_matched = problem_sku in matched_skus
        logger.info(f"{problem_sku}:")
        logger.info(f"  In datafeedwatch: {in_datafeedwatch}")
        logger.info(f"  In unmatched list: {in_unmatched}")
        logger.info(f"  In matched list: {in_matched}")
        if in_unmatched:
            details = [d for d in unmatched_details if problem_sku in d]
            logger.info(f"  Reason: {details}")
    
    return feed_entries

# ============================================================================
# FEED GENERATION FUNCTIONS
# ============================================================================

def upload_supplementary_feed(feed_entries: List[Dict[str, Any]]) -> bool:
    """Generate feed XML with categories, availability, and optional sale prices (NO images)"""
    
    # Build the XML feed content
    feed_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
    feed_content += '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">\n'
    feed_content += '<channel>\n'
    
    for entry in feed_entries:
        sku = entry['id']
        product_type = entry['product_type']
        availability = entry.get('availability', 'in stock')
        sale_price = entry.get('sale_price')
        
        feed_content += '  <item>\n'
        feed_content += f'    <g:id>{escape_xml(sku)}</g:id>\n'
        
        if product_type:
            feed_content += f'    <g:product_type>{escape_xml(product_type)}</g:product_type>\n'
        
        # Add availability status
        feed_content += f'    <g:availability>{escape_xml(availability)}</g:availability>\n'
        
        # Add sale price if applicable
        if sale_price:
            # Format as currency (e.g., 1149.00 AUD)
            feed_content += f'    <g:sale_price>{sale_price:.2f} AUD</g:sale_price>\n'
        
        feed_content += '  </item>\n'
    
    feed_content += '</channel>\n'
    feed_content += '</rss>\n'
    
    # Save feed to file
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    feed_filename = f"/tmp/neto_gmc_feed_{timestamp}.xml"
    
    try:
        with open(feed_filename, 'w') as f:
            f.write(feed_content)
        logger.info(f"✓ Feed XML generated: {feed_filename}")
        logger.info(f"  File size: {len(feed_content)} bytes")
        logger.info(f"  Entries: {len(feed_entries)}")
    except Exception as e:
        logger.error(f"Could not save feed file: {type(e).__name__}")
        return False
    
    return True

def escape_xml(text: str) -> str:
    """Escape special XML characters"""
    if not text:
        return ""
    
    replacements = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&apos;',
    }
    
    for char, escaped in replacements.items():
        text = text.replace(char, escaped)
    
    return text

# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    """Main execution function"""
    logger.info("=" * 70)
    logger.info("Neto to Google Merchant Center Category Feed Sync")
    logger.info(f"Started at {datetime.now().isoformat()}")
    logger.info("=" * 70)
    
    try:
        # Load image cache
        logger.info("")
        image_cache = load_image_cache()
        
        # Fetch datafeedwatch products first
        logger.info("")
        datafeedwatch_products = fetch_datafeedwatch_products()
        
        if not datafeedwatch_products:
            logger.error("No products fetched from datafeedwatch. Aborting.")
            return False
        
        # Fetch products from Neto
        logger.info("")
        products = fetch_all_products()
        
        if not products:
            logger.error("No products fetched from Neto. Aborting.")
            return False
        
        # Build feed with datafeedwatch product matching
        logger.info("")
        feed_entries = build_category_feed(products, datafeedwatch_products, image_cache)
        
        if not feed_entries:
            logger.error("No feed entries built. Aborting.")
            return False
        
        # Save updated cache
        logger.info("")
        save_image_cache(image_cache)
        
        # Generate feed file
        logger.info("")
        success = upload_supplementary_feed(feed_entries)
        
        if success:
            logger.info("=" * 70)
            logger.info("✓ SYNC COMPLETED SUCCESSFULLY")
            logger.info("  Feed generated and ready for workflow to commit to GitHub")
            logger.info("  Image cache updated and ready to commit")
            logger.info("=" * 70)
            return True
        else:
            logger.error("=" * 70)
            logger.error("✗ SYNC FAILED")
            logger.error("=" * 70)
            return False
    
    except Exception as e:
        logger.error("=" * 70)
        logger.error(f"✗ FATAL ERROR: {type(e).__name__}")
        logger.error("=" * 70)
        return False

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
