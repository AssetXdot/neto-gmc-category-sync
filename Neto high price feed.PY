#!/usr/bin/env python3
"""
Neto High-Price Products Supplementary Feed
Generates a complete supplementary feed for products >$200 not in datafeedwatch/PriceSpy.
Includes all product data: title, description, images, shipping, pricing, etc.
This is a SEPARATE feed from the category/availability feed.
"""

import os
import time
import requests
from datetime import datetime
from typing import List, Dict, Any
import logging
import xml.etree.ElementTree as ET
import re

# ============================================================================
# SECURE LOGGING
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
        
        # Mask API keys and credentials
        msg = re.sub(r'(NETOAPI_KEY|NETO_API_KEY)[:\s]*[^\s]+', r'\1=***MASKED***', msg, flags=re.IGNORECASE)
        msg = re.sub(r'(NETOAPI_USERNAME|API_USERNAME)[:\s]*[^\s]+', r'\1=***MASKED***', msg, flags=re.IGNORECASE)
        
        record.msg = msg
        record.args = ()
        return super().format(record)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
for handler in logger.handlers:
    handler.setFormatter(SecureFormatter('%(asctime)s - %(levelname)s - %(message)s'))

# ============================================================================
# CONFIGURATION
# ============================================================================

NETO_API_KEY = os.getenv('NETO_API_KEY', '').strip()
NETO_API_USERNAME = os.getenv('NETO_API_USERNAME', '').strip()
DATAFEEDWATCH_URL = os.getenv('DATAFEEDWATCH_URL', '').strip()

if not NETO_API_KEY:
    raise ValueError("NETO_API_KEY not set")
if not NETO_API_USERNAME:
    raise ValueError("NETO_API_USERNAME not set")
if not DATAFEEDWATCH_URL:
    raise ValueError("DATAFEEDWATCH_URL not set")

# ============================================================================
# NETO API
# ============================================================================

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

def fetch_datafeedwatch_products() -> Dict[str, bool]:
    """
    Load ALL datafeedwatch identifiers (SKU, GTIN, and product ID) so that
    products already in the primary PriceSpy feed are reliably excluded.
    Matching by SKU alone is NOT enough - many datafeedwatch products are
    keyed by GTIN, and missing them would create DUPLICATE products in GMC.
    """
    logger.info("Fetching datafeedwatch feed to identify excluded products...")
    
    try:
        response = requests.get(DATAFEEDWATCH_URL, timeout=30)
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        products = {}
        ns = {'g': 'http://base.google.com/ns/1.0'}
        
        for item in root.findall('.//item'):
            # Product ID (g:id)
            id_elem = item.find('g:id', ns)
            if id_elem is not None and id_elem.text and id_elem.text.strip():
                products[id_elem.text.strip()] = True
            
            # GTIN
            gtin_elem = item.find('g:gtin', ns)
            if gtin_elem is not None and gtin_elem.text and gtin_elem.text.strip():
                products[gtin_elem.text.strip()] = True
            
            # SKU
            sku_elem = item.find('g:SKU', ns)
            if sku_elem is not None and sku_elem.text and sku_elem.text.strip():
                products[sku_elem.text.strip()] = True
        
        logger.info(f"Datafeedwatch identifiers loaded: {len(products)} (SKUs + GTINs + IDs)")
        return products
    
    except Exception as e:
        logger.error(f"Error fetching datafeedwatch feed: {type(e).__name__}")
        return {}

def fetch_all_products() -> List[Dict[str, Any]]:
    """Fetch all active products from Neto"""
    logger.info("Fetching products from Neto...")
    
    all_items = []
    page = 0
    
    while True:
        try:
            payload = {
                "Filter": {
                    "IsActive": "True",
                    "OutputSelector": [
                        "SKU", "Name", "Brand", "DefaultPrice", "Description", "ProductURL",
                        "ImageURL", "ShippingWeight", "ShippingHeight", "ShippingLength",
                        "ShippingWidth", "UPC", "UPC1", "UPC2", "UPC3", "Categories",
                        "Misc01", "Misc02", "AvailableSellQuantity"
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
            time.sleep(0.3)  # Rate limiting - be kind to the Neto API
            
        except Exception as e:
            logger.error(f"Error fetching page {page}: {type(e).__name__}")
            break
    
    logger.info(f"Total products fetched: {len(all_items)}")
    return all_items

# ============================================================================
# HIGH-PRICE PRODUCT FILTERING
# ============================================================================

def get_first_gtin(product: Dict[str, Any]) -> str:
    """
    Get a VALID GTIN from UPC fields - try UPC, UPC1, UPC2, UPC3 in order.
    Google validates GTINs strictly: must be 8, 12, 13, or 14 digits.
    Invalid GTINs cause item disapprovals, so return None unless valid.
    """
    for field in ["UPC", "UPC1", "UPC2", "UPC3"]:
        value = str(product.get(field, "") or "").strip()
        if value and value.isdigit() and len(value) in (8, 12, 13, 14):
            return value
    return None

def get_all_identifiers(product: Dict[str, Any]) -> List[str]:
    """Get all identifiers (SKU + all UPC values) used to match against datafeedwatch"""
    identifiers = []
    sku = str(product.get("SKU", "") or "").strip()
    if sku:
        identifiers.append(sku)
    for field in ["UPC", "UPC1", "UPC2", "UPC3"]:
        value = str(product.get(field, "") or "").strip()
        if value:
            identifiers.append(value)
    return identifiers

def strip_html(text: str) -> str:
    """
    Convert an HTML description to plain text for Google.
    Google recommends plain text; literal HTML tags may display as text.
    """
    if not text:
        return ""
    # Convert common block-level endings to spaces so words don't run together
    text = re.sub(r'<\s*(br|/p|/div|/li|/h[1-6]|/tr)\s*/?\s*>', ' ', text, flags=re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode common HTML entities
    entities = {
        '&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>',
        '&quot;': '"', '&#39;': "'", '&apos;': "'", '&rsquo;': "'",
        '&lsquo;': "'", '&rdquo;': '"', '&ldquo;': '"', '&ndash;': '-',
        '&mdash;': '-', '&hellip;': '...'
    }
    for entity, char in entities.items():
        text = text.replace(entity, char)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def extract_categories(product: Dict[str, Any]) -> List[str]:
    """
    Extract category names from Neto's nested Categories structure, sorted by priority.
    
    Actual Neto API structure:
    {
      "Categories": [
        {
          "Category": {
            "CategoryID": "123",
            "Priority": "10",
            "CategoryName": "Category Name"
          }
        }
      ]
    }
    
    Returns: List of category names sorted by Priority (highest first)
    """
    categories_with_priority = []
    cats_raw = product.get("Categories", [])
    
    if isinstance(cats_raw, dict):
        cats_raw = [cats_raw]
    
    # Extract CategoryName and Priority from each wrapper
    for wrapper in cats_raw:
        if not isinstance(wrapper, dict):
            continue
        
        # "Category" is a dict for single-category products,
        # or a LIST of dicts for multi-category products - handle both
        cat_obj = wrapper.get("Category")
        if isinstance(cat_obj, dict):
            cat_list = [cat_obj]
        elif isinstance(cat_obj, list):
            cat_list = cat_obj
        else:
            continue
        
        for cat in cat_list:
            if not isinstance(cat, dict):
                continue
            
            name = cat.get("CategoryName", "").strip()
            priority_str = cat.get("Priority", "0")
            
            # Convert priority to int for sorting (API returns it as string)
            try:
                priority = int(priority_str)
            except (ValueError, TypeError):
                priority = 0
            
            if name:
                categories_with_priority.append({
                    "name": name,
                    "priority": priority
                })
    
    # Sort by priority descending (highest first)
    categories_with_priority.sort(key=lambda x: x["priority"], reverse=True)
    
    return [cat["name"] for cat in categories_with_priority]

def build_high_price_feed(products: List[Dict[str, Any]], datafeedwatch_skus: Dict[str, bool]) -> List[Dict[str, Any]]:
    """Build feed entries for high-price products NOT in datafeedwatch"""
    logger.info("Building high-price feed...")
    
    feed_entries = []
    products_processed = 0
    products_included = 0
    
    for product in products:
        sku = product.get("SKU", "").strip()
        if not sku:
            continue
        
        products_processed += 1
        
        # Skip if in datafeedwatch by ANY identifier (SKU or any UPC/GTIN).
        # This prevents the same physical product being listed twice in GMC
        # (once via PriceSpy primary feed + once via this feed = duplicates)
        if any(ident in datafeedwatch_skus for ident in get_all_identifiers(product)):
            logger.debug(f"SKU {sku}: Skipped (in datafeedwatch)")
            continue
        
        # Check if price > $200
        default_price = product.get("DefaultPrice")
        if not default_price:
            logger.debug(f"SKU {sku}: Skipped (no price)")
            continue
        
        try:
            price_float = float(default_price)
            if price_float <= 200:
                logger.debug(f"SKU {sku}: Skipped (price ${price_float} <= $200)")
                continue
        except (ValueError, TypeError):
            logger.debug(f"SKU {sku}: Skipped (invalid price)")
            continue
        
        # INCLUDE: High-price product not in datafeedwatch
        products_included += 1
        
        # Extract product data
        name = product.get("Name", "").strip()
        brand = product.get("Brand", "").strip()
        # Neto's Description field typically contains HTML - Google wants
        # plain text, so strip tags and decode entities
        description = strip_html(product.get("Description", ""))
        product_url = product.get("ProductURL", "").strip()
        image_url = product.get("ImageURL", "").strip()
        shipping_weight = product.get("ShippingWeight")
        shipping_height = product.get("ShippingHeight")
        shipping_length = product.get("ShippingLength")
        shipping_width = product.get("ShippingWidth")
        gtin = get_first_gtin(product)
        
        # Extract categories sorted by priority (highest first), then join
        # into ONE hierarchy path so listing groups get multi-level subdivision
        categories = extract_categories(product)
        product_type = " > ".join(categories) if categories else ""
        
        # Determine availability
        qty_raw = product.get("AvailableSellQuantity")
        backorder_enabled = product.get("Misc01", "").strip().upper() in ("TRUE", "Y", "YES", "1")
        availability = "in stock"
        if qty_raw is not None:
            try:
                if float(qty_raw) == 0 and backorder_enabled:
                    availability = "out of stock"
            except (ValueError, TypeError):
                pass
        
        # Build feed entry
        entry = {
            "id": sku,  # Use SKU as product ID for high-price products
            "title": name,
            "description": description,
            "link": product_url,
            "image_link": image_url,
            "price": price_float,
            "availability": availability,
            "condition": "New",
            "brand": brand,
            "gtin": gtin,
            "mpn": sku,
            "product_type": product_type,
            "shipping_weight": shipping_weight,
            "shipping_height": shipping_height,
            "shipping_length": shipping_length,
            "shipping_width": shipping_width,
        }
        
        feed_entries.append(entry)
        logger.debug(f"SKU {sku}: Included (price ${price_float})")
    
    logger.info(f"High-price feed built:")
    logger.info(f"  Products processed: {products_processed}")
    logger.info(f"  Products included (>$200): {products_included}")
    logger.info(f"  Total feed entries: {len(feed_entries)}")
    
    return feed_entries

# ============================================================================
# XML GENERATION
# ============================================================================

def escape_xml(text: str) -> str:
    """Escape special XML characters"""
    if not text:
        return ""
    
    replacements = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&apos;'
    }
    
    for char, escaped in replacements.items():
        text = text.replace(char, escaped)
    
    return text

def generate_high_price_feed(feed_entries: List[Dict[str, Any]]) -> bool:
    """Generate high-price feed XML"""
    logger.info("Generating high-price feed XML...")
    
    feed_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
    feed_content += '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">\n'
    feed_content += '<channel>\n'
    feed_content += '  <title>The BBQ Store - High-Price Products</title>\n'
    feed_content += '  <link>https://www.thebbqstore.com.au</link>\n'
    feed_content += '  <description>Products over $200 not managed by PriceSpy</description>\n'
    
    for entry in feed_entries:
        feed_content += '  <item>\n'
        feed_content += f'    <g:id>{escape_xml(entry["id"])}</g:id>\n'
        
        if entry.get('title'):
            feed_content += f'    <g:title>{escape_xml(entry["title"])}</g:title>\n'
        
        if entry.get('description'):
            feed_content += f'    <g:description>{escape_xml(entry["description"][:5000])}</g:description>\n'
        
        if entry.get('link'):
            feed_content += f'    <g:link>{escape_xml(entry["link"])}</g:link>\n'
        
        if entry.get('image_link'):
            feed_content += f'    <g:image_link>{escape_xml(entry["image_link"])}</g:image_link>\n'
        
        if entry.get('price'):
            feed_content += f'    <g:price>{entry["price"]:.2f} AUD</g:price>\n'
        
        if entry.get('availability'):
            feed_content += f'    <g:availability>{escape_xml(entry["availability"])}</g:availability>\n'
        
        if entry.get('condition'):
            feed_content += f'    <g:condition>{escape_xml(entry["condition"])}</g:condition>\n'
        
        if entry.get('brand'):
            feed_content += f'    <g:brand>{escape_xml(entry["brand"])}</g:brand>\n'
        
        # GTIN - only if available
        if entry.get('gtin'):
            feed_content += f'    <g:gtin>{escape_xml(entry["gtin"])}</g:gtin>\n'
        
        # MPN (SKU) - always include
        if entry.get('mpn'):
            feed_content += f'    <g:mpn>{escape_xml(entry["mpn"])}</g:mpn>\n'
        
        # Single joined hierarchy path, priority-ordered
        if entry.get('product_type'):
            feed_content += f'    <g:product_type>{escape_xml(entry["product_type"])}</g:product_type>\n'
        
        # Shipping dimensions (if available)
        if entry.get('shipping_weight'):
            try:
                weight_kg = float(entry['shipping_weight'])
                # Google accepts kg
                feed_content += f'    <g:shipping_weight>{weight_kg:.2f} kg</g:shipping_weight>\n'
            except (ValueError, TypeError):
                pass
        
        # Shipping height (in cm - assuming Neto provides decimals that need conversion)
        if entry.get('shipping_height'):
            try:
                height = float(entry['shipping_height'])
                # If value is small (< 10), assume it's in meters, convert to cm
                if height < 10:
                    height_cm = height * 100
                else:
                    height_cm = height
                feed_content += f'    <g:shipping_height>{height_cm:.0f} cm</g:shipping_height>\n'
            except (ValueError, TypeError):
                pass
        
        if entry.get('shipping_length'):
            try:
                length = float(entry['shipping_length'])
                if length < 10:
                    length_cm = length * 100
                else:
                    length_cm = length
                feed_content += f'    <g:shipping_length>{length_cm:.0f} cm</g:shipping_length>\n'
            except (ValueError, TypeError):
                pass
        
        if entry.get('shipping_width'):
            try:
                width = float(entry['shipping_width'])
                if width < 10:
                    width_cm = width * 100
                else:
                    width_cm = width
                feed_content += f'    <g:shipping_width>{width_cm:.0f} cm</g:shipping_width>\n'
            except (ValueError, TypeError):
                pass
        
        feed_content += '  </item>\n'
    
    feed_content += '</channel>\n'
    feed_content += '</rss>\n'
    
    # Save feed to file
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    feed_filename = f"/tmp/neto_high_price_feed_{timestamp}.xml"
    
    try:
        with open(feed_filename, 'w') as f:
            f.write(feed_content)
        logger.info(f"✓ Feed XML generated: {feed_filename}")
        logger.info(f"  File size: {len(feed_content)} bytes")
        logger.info(f"  Entries: {len(feed_entries)}")
        return True
    except Exception as e:
        logger.error(f"Could not save feed file: {type(e).__name__}")
        return False

# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main execution"""
    logger.info("=" * 70)
    logger.info("Neto High-Price Products Supplementary Feed")
    logger.info(f"Started at {datetime.now().isoformat()}")
    logger.info("=" * 70)
    
    try:
        # Load datafeedwatch SKUs
        logger.info("")
        datafeedwatch_skus = fetch_datafeedwatch_products()
        
        if not datafeedwatch_skus:
            logger.error("No products loaded from datafeedwatch. Aborting.")
            return False
        
        # Fetch all Neto products
        logger.info("")
        products = fetch_all_products()
        
        if not products:
            logger.error("No products fetched from Neto. Aborting.")
            return False
        
        # Build high-price feed
        logger.info("")
        feed_entries = build_high_price_feed(products, datafeedwatch_skus)
        
        if not feed_entries:
            logger.warning("No high-price products found (>$200). Creating empty feed.")
        
        # Generate feed XML
        logger.info("")
        success = generate_high_price_feed(feed_entries)
        
        if success:
            logger.info("=" * 70)
            logger.info("✓ HIGH-PRICE FEED COMPLETED SUCCESSFULLY")
            logger.info("=" * 70)
            return True
        else:
            logger.error("=" * 70)
            logger.error("✗ FEED GENERATION FAILED")
            logger.error("=" * 70)
            return False
    
    except Exception as e:
        logger.error("=" * 70)
        logger.error(f"✗ FATAL ERROR: {type(e).__name__}: {str(e)}")
        logger.error("=" * 70)
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
