#!/usr/bin/env python3
"""
Neto to Google Merchant Center Category Feed
Extracts multiple categories from Neto products and uploads to GMC as supplementary feed
Runs daily via GitHub Actions
"""

import os
import json
import time
import requests
from datetime import datetime
from typing import List, Dict, Any
import logging
import xml.etree.ElementTree as ET

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION - Read from environment variables
# ============================================================================

NETO_API_KEY = os.getenv('NETO_API_KEY', '').strip()
NETO_API_USERNAME = os.getenv('NETO_API_USERNAME', '').strip()
GOOGLE_MERCHANT_ID = os.getenv('GOOGLE_MERCHANT_ID', '').strip()
GOOGLE_CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON', '').strip()
DATAFEEDWATCH_URL = os.getenv('DATAFEEDWATCH_URL', '').strip()

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
    raise ValueError(f"GOOGLE_CREDENTIALS_JSON is not valid JSON: {e}")

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
                products[gtin] = entry  # Map by GTIN
            if sku:
                entry['sku'] = sku
                products[sku] = entry   # Map by SKU
            
            # Also map by product ID itself
            products[product_id] = entry
        
        logger.info(f"Datafeedwatch products loaded: {len(products)} mappings from items")
        return products
    
    except Exception as e:
        logger.error(f"Error fetching datafeedwatch feed: {e}")
        import traceback
        logger.error(traceback.format_exc())
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
        logger.error(f"Neto API error: {e}")
        raise

def extract_product_images(item: Dict[str, Any]) -> List[str]:
    """
    Extract all image URLs from Neto product.
    Returns list of URLs: [main_image, alt_1, alt_2, ... alt_12]
    """
    images = []
    
    # Debug: Log all fields that contain "image" or "alt" (case insensitive)
    image_fields = [k for k in item.keys() if 'image' in k.lower() or 'alt' in k.lower()]
    if image_fields:
        logger.debug(f"Found image-related fields: {image_fields}")
    
    # Get main image (try multiple possible field names)
    main_image = None
    for field_name in ["ImageURL", "Image", "image_url", "MainImage", "main_image"]:
        main_image = item.get(field_name, "").strip()
        if main_image:
            logger.debug(f"Found main image in field '{field_name}': {main_image[:50]}...")
            images.append(main_image)
            break
    
    if not main_image:
        logger.debug(f"No main image found. Available fields: {list(item.keys())[:20]}")
    
    # Get alternative images (Alt1, Alt2, Alt3, ... Alt12)
    for i in range(1, 13):
        for field_pattern in [f"Alt{i}", f"alt{i}", f"Alt {i}", f"Image{i}", f"image{i}"]:
            alt_image = item.get(field_pattern, "").strip()
            if alt_image:
                logger.debug(f"Found alt image {i} in field '{field_pattern}'")
                images.append(alt_image)
                break
    
    if images:
        logger.debug(f"Extracted {len(images)} images total")
    
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
    
    # Log all fields in the item to see what's available
    logger.debug(f"Neto item fields: {list(item.keys())}")
    
    # Try to get UPC/GTIN/Barcode from various field names
    for field in ["UPC", "GTIN", "Barcode", "EAN", "ProductBarcode", "Ean"]:
        value = item.get(field, "").strip()
        if value:
            result['upc'] = value
            logger.debug(f"Found {field}: {value}")
            break
    
    if 'upc' not in result:
        logger.debug(f"No UPC found for SKU {sku}. Available fields: {item.keys()}")
    
    return result

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
            logger.info(f"DEBUG: Full Neto response page {page}: {data}")
            
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
            logger.error(f"Error fetching page {page}: {e}")
            break
    
    logger.info(f"Total products fetched: {len(all_items)}")
    return all_items

def build_category_feed(products: List[Dict[str, Any]], datafeedwatch_products: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build feed entries matching Neto products with datafeedwatch product IDs.
    Applies conditional availability: out of stock if backorder disabled AND qty=0, else in stock
    
    Format: [{"id": "datafeedwatch_id", "product_type": "Cat1, Cat2, Cat3", "availability": "..."}, ...]
    """
    feed_entries = []
    products_with_categories = 0
    products_without_categories = 0
    products_not_in_datafeedwatch = 0
    
    matched_skus = []
    unmatched_skus = []
    unmatched_details = []
    
    for i, product in enumerate(products):
        # Extract Neto identifiers
        ids = extract_product_ids(product)
        sku = ids.get('sku', 'NO_SKU')
        upc = ids.get('upc', 'NO_UPC')
        
        # Try to match with datafeedwatch using UPC
        datafeedwatch_id = None
        matched_key = None
        
        if upc != 'NO_UPC' and upc in datafeedwatch_products:
            datafeedwatch_id = datafeedwatch_products[upc]['id']
            matched_key = f"UPC:{upc}"
        elif sku != 'NO_SKU' and sku in datafeedwatch_products:
            # Fallback to SKU match
            datafeedwatch_id = datafeedwatch_products[sku]['id']
            matched_key = f"SKU:{sku}"
        
        # Skip products not in datafeedwatch
        if not datafeedwatch_id:
            products_not_in_datafeedwatch += 1
            unmatched_skus.append(sku)
            unmatched_details.append({
                'sku': sku,
                'upc': upc,
                'name': product.get('Name', '')[:50]
            })
            if i < 5:  # Log first 5 unmatched for debugging
                logger.info(f"  UNMATCHED: SKU={sku}, UPC={upc}, Name={product.get('Name', '')[:50]}")
            continue
        
        matched_skus.append((sku, matched_key))
        
        # Extract categories
        categories = extract_categories(product)
        
        # Extract images from Neto
        images = extract_product_images(product)
        
        if categories:
            products_with_categories += 1
            product_type = ", ".join(categories)
        else:
            products_without_categories += 1
            product_type = ""
        
        # Determine availability based on backorder setting and stock level
        # Check if backorder is enabled and qty on hand is 0
        backorder_enabled = product.get("EnableBackorderButton", "False")
        qty_on_hand = product.get("QtyOnHand", 0)
        
        # Convert to appropriate types for comparison
        if isinstance(backorder_enabled, str):
            backorder_enabled = backorder_enabled.lower() == "true"
        if isinstance(qty_on_hand, str):
            try:
                qty_on_hand = int(qty_on_hand)
            except (ValueError, TypeError):
                qty_on_hand = 0
        
        # Logic: if backorder is enabled AND stock is 0, mark as out of stock
        # Otherwise, mark as in stock
        if backorder_enabled and qty_on_hand == 0:
            availability = "out of stock"
        else:
            availability = "in stock"
        
        feed_entries.append({
            "id": datafeedwatch_id,
            "product_type": product_type,
            "availability": availability,
            "images": images  # Add images to feed entry
        })
    
    # Log debug info
    logger.info(f"Products with categories: {products_with_categories}")
    logger.info(f"Products without categories: {products_without_categories}")
    logger.info(f"Products not in datafeedwatch: {products_not_in_datafeedwatch}")
    logger.info(f"Total feed entries: {len(feed_entries)}")
    logger.info(f"")
    logger.info(f"Sample matched SKUs: {matched_skus[:5]}")
    logger.info(f"Sample unmatched (first 5): {unmatched_details[:5]}")
    logger.info(f"Total unmatched: {len(unmatched_skus)}")
    logger.info(f"Datafeedwatch keys (sample): {list(datafeedwatch_products.keys())[:20]}")
    logger.info(f"Total datafeedwatch keys: {len(datafeedwatch_products)}")
    
    return feed_entries

# ============================================================================
# GOOGLE MERCHANT CENTER API FUNCTIONS
# ============================================================================

def get_google_access_token() -> str:
    """Get OAuth access token for Google API using service account"""
    import jwt
    
    try:
        now = int(time.time())
        claim = {
            'iss': GOOGLE_CREDS['client_email'],
            'scope': 'https://www.googleapis.com/auth/content',
            'aud': 'https://oauth2.googleapis.com/token',
            'exp': now + 3600,
            'iat': now,
        }
        
        token = jwt.encode(
            claim,
            GOOGLE_CREDS['private_key'],
            algorithm='RS256'
        )
        
        response = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                'assertion': token,
            },
            timeout=30
        )
        response.raise_for_status()
        
        return response.json()['access_token']
    
    except Exception as e:
        logger.error(f"Failed to get Google access token: {e}")
        raise

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
        
        # Add availability status - conditional based on backorder and stock
        feed_content += f'    <g:availability>{escape_xml(availability)}</g:availability>\n'
        
        # Add images - first one is main image, rest are additional
        if images:
            feed_content += f'    <g:image_link>{escape_xml(images[0])}</g:image_link>\n'
            
            # Add additional images (alt 1-10)
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
        logger.error(f"Could not save feed file: {e}")
        return False
    
    logger.info("=" * 70)
    logger.info("NEXT STEP: Upload feed to Google Merchant Center")
    logger.info("=" * 70)
    logger.info(f"1. Go to: https://merchantcenter.google.com")
    logger.info(f"2. Click: Products → Feeds")
    logger.info(f"3. Create or select 'Neto Categories' feed")
    logger.info(f"4. Upload the XML file: {feed_filename}")
    logger.info("=" * 70)
    
    return True

def get_existing_feeds(access_token: str) -> Dict[str, str]:
    """Get existing feeds from GMC to find our supplementary feed"""
    url = f"https://www.googleapis.com/content/v2.1/{GOOGLE_MERCHANT_ID}/datafeeds"
    
    headers = {
        'Authorization': f'Bearer {access_token}',
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        feeds = response.json().get('resources', [])
        feed_ids = {}
        
        for feed in feeds:
            # Look for our supplementary feed by name
            if feed.get('name', '').lower() == 'neto categories':
                feed_ids['neto_categories'] = feed.get('id')
        
        return feed_ids
    
    except Exception as e:
        logger.warning(f"Could not retrieve existing feeds: {e}")
        return {}

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
        feed_entries = build_category_feed(products, datafeedwatch_products)
        
        if not feed_entries:
            logger.error("No feed entries built. Aborting.")
            return False
        
        # Generate feed file for manual upload
        logger.info("")
        success = upload_supplementary_feed(feed_entries)
        
        if success:
            logger.info("=" * 70)
            logger.info("✓ SYNC COMPLETED SUCCESSFULLY")
            logger.info("=" * 70)
            return True
        else:
            logger.error("=" * 70)
            logger.error("✗ SYNC FAILED")
            logger.error("=" * 70)
            return False
    
    except Exception as e:
        logger.error("=" * 70)
        logger.error(f"✗ FATAL ERROR: {e}")
        logger.error("=" * 70)
        return False

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
