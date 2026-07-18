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
    """Load image cache from file if it exists"""
    if os.path.exists(IMAGE_CACHE_FILE):
        try:
            with open(IMAGE_CACHE_FILE, 'r') as f:
                cache = json.load(f)
            logger.info(f"Loaded image cache: {len(cache)} products cached")
            return cache
        except Exception as e:
            logger.warning(f"Could not load cache: {type(e).__name__}, starting fresh")
            return {}
    return {}

def save_image_cache(cache: Dict[str, Dict[str, Any]]) -> bool:
    """Save image cache to file"""
    try:
        with open(IMAGE_CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
        logger.info(f"Saved image cache: {len(cache)} products")
        return True
    except Exception as e:
        logger.error(f"Could not save cache: {type(e).__name__}")
        return False

def get_product_hash(product: Dict[str, Any]) -> str:
    """
    Generate hash of product data to detect changes.
    Hash includes: SKU, Name, Price (key fields that affect image availability).
    If any of these change, we re-check images.
    """
    sku = product.get('SKU', '')
    name = product.get('Name', '')
    price = product.get('DefaultPrice', '')
    
    data = f"{sku}|{name}|{price}"
    return hashlib.md5(data.encode()).hexdigest()

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
            if sku:
                entry['sku'] = sku
                products[sku] = entry
            
            products[product_id] = entry
        
        logger.info(f"Datafeedwatch products loaded: {len(products)} mappings from items")
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
    
    # Normalize to list
    if isinstance(cats_raw, dict):
        cats_raw = [cats_raw]
    
    # Extract CategoryName from each category
    for wrapper in cats_raw:
        if not isinstance(wrapper, dict):
            continue
        
        cat_list = wrapper.get("Category", [])
        if isinstance(cat_list, dict):
            cat_list = [cat_list]
        
        for cat in cat_list:
            if isinstance(cat, dict):
                name = cat.get("CategoryName", "").strip()
                if name:
                    categories.append(name)
    
    # Apply normalization to standardize singular/plural forms
    if normalize:
        categories = normalize_category_list(categories)
    
    return categories

# REMOVED: No more HEAD request checking - too slow and gets blocked
# Just build URLs for all formats in order, GMC will validate them

def build_product_images(sku: str, product: Dict[str, Any], cache: Dict[str, Dict[str, Any]]) -> List[str]:
    """
    Build image URLs for a product based on its SKU.
    
    STRATEGY: Just build URLs for all common formats.
    Don't check if they exist - GMC handles 404s automatically.
    
    URL pattern: https://www.thebbqstore.com.au/assets/{thumb}/{SKU}.{format}
    
    Tries formats in order: jpg, png, webp, gif
    Builds URLs for all 4 formats for both main and alts.
    
    Returns: [main urls, alt_1 urls, alt_2 urls, ..., alt_12 urls]
            Each position has 4 format variations
    """
    images = []
    
    if not sku:
        return images
    
    base_url = "https://www.thebbqstore.com.au/assets"
    formats = ['jpg', 'png', 'webp', 'gif']
    
    # Calculate current product hash to detect changes
    current_hash = get_product_hash(product)
    
    # Check cache for this SKU
    if sku in cache:
        cached_data = cache[sku]
        cached_hash = cached_data.get('hash')
        cached_images = cached_data.get('images', [])
        
        # If product hasn't changed, use cached images
        if cached_hash == current_hash and cached_images:
            logger.debug(f"SKU {sku}: Product unchanged, using cached images ({len(cached_images)} images)")
            return cached_images
        
        # If product changed, re-build (fall through)
        logger.debug(f"SKU {sku}: Product changed, re-building image URLs")
    
    # Build URLs for main image - try all 4 formats
    main_base = f"{base_url}/full/{sku}"
    for fmt in formats:
        url = f"{main_base}.{fmt}"
        images.append(url)
    
    # Build URLs for alt 1-12 - try all 4 formats for each
    for i in range(1, 13):
        alt_base = f"{base_url}/alt_{i}/{sku}"
        for fmt in formats:
            url = f"{alt_base}.{fmt}"
            images.append(url)
    
    logger.debug(f"SKU {sku}: Built {len(images)} image URLs (4 formats each for main + 12 alts)")
    
    # Update cache with hash and new images
    cache[sku] = {
        'images': images,
        'hash': current_hash,
        'last_checked': datetime.now().isoformat(),
        'count': len(images)
    }
    
    return images

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
                        "SKU", "Name", "Brand", "DefaultPrice", "Categories"
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
    
    Format: [{"id": "datafeedwatch_id", "product_type": "Cat1, Cat2, Cat3", "availability": "...", "images": [...]}, ...]
    """
    feed_entries = []
    products_with_categories = 0
    products_without_categories = 0
    products_not_in_datafeedwatch = 0
    matched_skus = []
    unmatched_skus = []
    unmatched_details = []
    total_images_built = 0
    cache_hits = 0
    cache_misses = 0
    products_changed = 0
    
    for product in products:
        sku = product.get("SKU", "").strip()
        if not sku:
            continue
        
        # Extract categories from Neto's nested structure
        categories = extract_categories(product, normalize=True)
        
        if not categories:
            products_without_categories += 1
            unmatched_skus.append(sku)
            unmatched_details.append(f"{sku} (no categories)")
            continue
        
        products_with_categories += 1
        
        # Try to find in datafeedwatch by SKU, GTIN, or product ID
        datafeedwatch_id = None
        product_ids = extract_product_ids(product)
        
        # Try SKU first
        if sku in datafeedwatch_products:
            datafeedwatch_id = datafeedwatch_products[sku].get('id')
            matched_skus.append(sku)
        # Try UPC
        elif 'upc' in product_ids and product_ids['upc'] in datafeedwatch_products:
            datafeedwatch_id = datafeedwatch_products[product_ids['upc']].get('id')
            matched_skus.append(sku)
        
        if not datafeedwatch_id:
            products_not_in_datafeedwatch += 1
            unmatched_skus.append(sku)
            unmatched_details.append(f"{sku} (not in datafeedwatch)")
            continue
        
        # Build image URLs using smart cache with change detection
        images = build_product_images(sku, product, cache)
        
        # Track cache hit/miss/change
        if sku in cache:
            if cache[sku].get('hash') == get_product_hash(product):
                cache_hits += 1
            else:
                cache_misses += 1
                products_changed += 1
        else:
            cache_misses += 1
        
        total_images_built += len(images)
        
        # Build product type string from categories
        product_type = " > ".join(categories)
        
        # Determine availability
        availability = "in stock"  # Default
        
        feed_entries.append({
            "id": datafeedwatch_id,
            "product_type": product_type,
            "availability": availability,
            "images": images
        })
    
    # Log debug info
    logger.info(f"Products with categories: {products_with_categories}")
    logger.info(f"Products without categories: {products_without_categories}")
    logger.info(f"Products not in datafeedwatch: {products_not_in_datafeedwatch}")
    logger.info(f"Total feed entries: {len(feed_entries)}")
    logger.info(f"Total image URLs built: {total_images_built}")
    logger.info(f"  (4 formats × main + 12 alts = 52 URLs per product)")
    logger.info(f"Cache hits (unchanged): {cache_hits} | Cache misses/changes: {cache_misses} | Products changed: {products_changed}")
    logger.info(f"")
    logger.info(f"Sample matched SKUs: {matched_skus[:5]}")
    logger.info(f"Sample unmatched (first 5): {unmatched_details[:5]}")
    logger.info(f"Total unmatched: {len(unmatched_skus)}")
    logger.info(f"Datafeedwatch keys (sample): {list(datafeedwatch_products.keys())[:20]}")
    logger.info(f"Total datafeedwatch keys: {len(datafeedwatch_products)}")
    
    return feed_entries

# ============================================================================
# FEED GENERATION FUNCTIONS
# ============================================================================

def upload_supplementary_feed(feed_entries: List[Dict[str, Any]]) -> bool:
    """Generate feed XML with categories, availability, and all product images from Neto"""
    
    # Build the XML feed content
    feed_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
    feed_content += '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">\n'
    feed_content += '<channel>\n'
    
    for entry in feed_entries:
        sku = entry['id']
        product_type = entry['product_type']
        availability = entry.get('availability', 'in stock')
        images = entry.get('images', [])
        
        feed_content += '  <item>\n'
        feed_content += f'    <g:id>{escape_xml(sku)}</g:id>\n'
        
        if product_type:
            feed_content += f'    <g:product_type>{escape_xml(product_type)}</g:product_type>\n'
        
        # Add availability status
        feed_content += f'    <g:availability>{escape_xml(availability)}</g:availability>\n'
        
        # Add images - first one is main image, rest are additional
        if images:
            feed_content += f'    <g:image_link>{escape_xml(images[0])}</g:image_link>\n'
            
            # Add additional images (alt 1-12)
            for alt_image in images[1:]:
                feed_content += f'    <g:additional_image_link>{escape_xml(alt_image)}</g:additional_image_link>\n'
        
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
