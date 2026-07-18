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
import subprocess
from datetime import datetime
from typing import List, Dict, Any
import logging
import xml.etree.ElementTree as ET

# Setup logging with custom formatter to mask secrets
class SecureFormatter(logging.Formatter):
    """Custom formatter that masks sensitive data in logs"""
    
    def format(self, record):
        # Mask common sensitive patterns
        msg = str(record.msg)
        if record.args:
            try:
                msg = msg % record.args
            except (TypeError, ValueError):
                pass
        
        # Mask API keys, tokens, and credentials
        import re
        msg = re.sub(r'(NETOAPI_KEY|NETO_API_KEY)[:\s]*[^\s]+', r'\1=***MASKED***', msg, flags=re.IGNORECASE)
        msg = re.sub(r'(NETOAPI_USERNAME|API_USERNAME)[:\s]*[^\s]+', r'\1=***MASKED***', msg, flags=re.IGNORECASE)
        msg = re.sub(r'(Authorization|Bearer)[:\s]*[^\s]+', r'\1: ***MASKED***', msg, flags=re.IGNORECASE)
        msg = re.sub(r'(password|secret|token)[=:\s]*[^\s]+', r'\1=***MASKED***', msg, flags=re.IGNORECASE)
        
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
# CONFIGURATION - Read from environment variables (NOT hardcoded)
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
    logger.error(f"GOOGLE_CREDENTIALS_JSON is not valid JSON")
    raise ValueError("Invalid GOOGLE_CREDENTIALS_JSON")

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
    
    category_lower = category.lower().strip()
    original_category = category
    
    for singular, plural in singular_to_plural.items():
        if category_lower.endswith(' ' + singular) or category_lower == singular:
            if category_lower == singular:
                normalized = plural
            else:
                normalized = category[:-len(singular)] + plural
            
            logger.debug(f"Normalized category: '{original_category}' → '{normalized}'")
            return normalized
    
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
        
        if isinstance(data, dict) and 'Error' in data:
            error_msg = data['Error'].get('Message', 'Unknown error')
            raise ValueError(f"Neto API error: {error_msg}")
        
        return data
    except requests.exceptions.RequestException as e:
        logger.error(f"Neto API error: {type(e).__name__} - HTTP request failed")
        raise

def extract_product_images(item: Dict[str, Any]) -> List[str]:
    """
    Extract all image URLs from Neto product.
    Returns list of URLs: [main_image, alt_1, alt_2, ... alt_12]
    
    Neto's Images field is structured as a list of image objects with URL keys.
    Falls back to individual Alt1-Alt12 fields if Images list not available.
    """
    images = []
    
    try:
        images_field = item.get("Images", [])
        
        if isinstance(images_field, list):
            logger.debug(f"Processing Images list with {len(images_field)} items")
            for img_obj in images_field:
                if isinstance(img_obj, dict):
                    url = img_obj.get("URL") or img_obj.get("url") or img_obj.get("ImageURL")
                    if url and isinstance(url, str):
                        url = url.strip()
                        if url and url not in images:
                            images.append(url)
                            logger.debug(f"Found image URL")
                elif isinstance(img_obj, str):
                    url = img_obj.strip()
                    if url and url not in images:
                        images.append(url)
                        logger.debug(f"Found image URL")
        
        elif isinstance(images_field, dict):
            url = images_field.get("URL") or images_field.get("url") or images_field.get("ImageURL")
            if url and isinstance(url, str):
                url = url.strip()
                if url:
                    images.append(url)
                    logger.debug(f"Found image URL")
        
        if not images:
            logger.debug("No Images list found, trying individual Alt fields...")
            
            for field_name in ["ImageURL", "Image", "image_url", "MainImage", "main_image", "product.mainImage.URL"]:
                main_image = item.get(field_name, "").strip()
                if main_image:
                    images.append(main_image)
                    logger.debug(f"Found main image")
                    break
            
            for i in range(1, 13):
                for field_name in [f"Alt{i}", f"alt{i}", f"Alt{i} Image", f"Image{i}"]:
                    alt_image = item.get(field_name, "").strip()
                    if alt_image:
                        images.append(alt_image)
                        logger.debug(f"Found alt image {i}")
                        break
        
        if not images:
            sku = item.get("SKU", "unknown")
            logger.debug(f"No images found for SKU {sku}")
        
        return images
    
    except Exception as e:
        sku = item.get("SKU", "unknown")
        logger.error(f"Error extracting images for SKU {sku}: {type(e).__name__}")
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
    """Fetch all products from Neto API"""
    products = []
    page = 0
    
    while True:
        logger.info(f"Fetching Neto products page {page}...")
        
        payload = {
            "Filter": {
                "Visible": ["True"],
                "Page": str(page),
                "Limit": "100",
                "OutputSelector": ["SKU", "Name", "Images", "CategoryName", "UPC", "GTIN", "ProductDescription"],
            }
        }
        
        try:
            data = neto_api_call("GetItem", payload)
            
            items = data.get("Item", [])
            if not items:
                break
            
            products.extend(items)
            logger.info(f"  Fetched {len(items)} products from page {page}")
            
            page += 1
            time.sleep(0.5)
        
        except Exception as e:
            logger.error(f"Error fetching page {page}: {type(e).__name__}")
            break
    
    logger.info(f"Total products fetched: {len(products)}")
    return products

def build_category_feed(products: List[Dict[str, Any]], datafeedwatch_products: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build supplementary feed with categories and images"""
    
    feed_entries = []
    matched_skus = []
    unmatched_skus = []
    products_with_categories = 0
    products_without_categories = 0
    products_not_in_datafeedwatch = 0
    
    for product in products:
        sku = product.get("SKU", "").strip()
        if not sku:
            continue
        
        # Get categories from Neto
        categories = product.get("CategoryName", [])
        if isinstance(categories, str):
            categories = [categories]
        categories = [c.strip() for c in categories if c.strip()]
        
        if not categories:
            products_without_categories += 1
            unmatched_skus.append(sku)
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
            continue
        
        # Extract images
        images = extract_product_images(product)
        
        # Normalize categories
        normalized_categories = normalize_category_list(categories)
        product_type = " > ".join(normalized_categories)
        
        # Determine availability
        backorder = product.get("BackorderStatus", "").strip().lower() == "true"
        availability = "backorder" if backorder else "in stock"
        
        feed_entries.append({
            "id": datafeedwatch_id,
            "product_type": product_type,
            "availability": availability,
            "images": images
        })
    
    logger.info(f"Products with categories: {products_with_categories}")
    logger.info(f"Products without categories: {products_without_categories}")
    logger.info(f"Products not in datafeedwatch: {products_not_in_datafeedwatch}")
    logger.info(f"Total feed entries: {len(feed_entries)}")
    logger.info(f"Sample matched SKUs: {matched_skus[:5]}")
    
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
        logger.error(f"Failed to get Google access token: {type(e).__name__}")
        raise

def upload_supplementary_feed(feed_entries: List[Dict[str, Any]]) -> tuple[bool, str]:
    """
    Generate feed XML with categories, availability, and all product images from Neto.
    Returns: (success: bool, feed_content: str)
    """
    
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
        
        feed_content += f'    <g:availability>{escape_xml(availability)}</g:availability>\n'
        
        if images:
            feed_content += f'    <g:image_link>{escape_xml(images[0])}</g:image_link>\n'
            
            for alt_image in images[1:]:
                feed_content += f'    <g:additional_image_link>{escape_xml(alt_image)}</g:additional_image_link>\n'
        
        feed_content += '  </item>\n'
    
    feed_content += '</channel>\n'
    feed_content += '</rss>\n'
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    feed_filename = f"/tmp/neto_gmc_feed_{timestamp}.xml"
    
    try:
        with open(feed_filename, 'w') as f:
            f.write(feed_content)
        logger.info(f"✓ Feed XML generated")
        logger.info(f"  File size: {len(feed_content)} bytes")
        logger.info(f"  Entries: {len(feed_entries)}")
    except Exception as e:
        logger.error(f"Could not save feed file: {type(e).__name__}")
        return False, ""
    
    return True, feed_content

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
            if feed.get('name', '').lower() == 'neto categories':
                feed_ids['neto_categories'] = feed.get('id')
        
        return feed_ids
    
    except Exception as e:
        logger.warning(f"Could not retrieve existing feeds: {type(e).__name__}")
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

def push_feed_to_github(feed_content: str, feed_filename: str = "neto_gmc_feed.xml") -> bool:
    """
    Commit and push the generated feed to GitHub repository.
    Uses GitHub Actions GITHUB_TOKEN for authentication (automatic).
    No credentials are logged.
    """
    try:
        logger.info("=" * 70)
        logger.info("Pushing feed to GitHub repository...")
        logger.info("=" * 70)
        
        repo_dir = os.getcwd()
        feed_path = os.path.join(repo_dir, "main", feed_filename)
        
        os.makedirs(os.path.dirname(feed_path), exist_ok=True)
        
        with open(feed_path, 'w') as f:
            f.write(feed_content)
        logger.info(f"✓ Feed file written to: main/{feed_filename}")
        
        subprocess.run(
            ["git", "config", "user.name", "GitHub Actions"],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.email", "actions@github.com"],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )
        logger.info("✓ Git user configured")
        
        subprocess.run(
            ["git", "add", f"main/{feed_filename}"],
            cwd=repo_dir,
            check=True,
            capture_output=True
        )
        logger.info(f"✓ Added {feed_filename} to git")
        
        commit_msg = f"Auto-update Neto GMC feed - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                logger.info("✓ No changes to commit (feed unchanged)")
                return True
            else:
                logger.error(f"Git commit failed - check repository state")
                return False
        
        logger.info(f"✓ Committed: {commit_msg}")
        
        push_result = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        
        if push_result.returncode != 0:
            logger.error(f"Git push failed - check repository permissions")
            return False
        
        logger.info("✓ Pushed to GitHub (main branch)")
        logger.info("=" * 70)
        logger.info("✓ Feed is now live at:")
        logger.info("  https://raw.githubusercontent.com/AssetXdot/neto-gmc-category-sync/main/neto_gmc_feed.xml")
        logger.info("=" * 70)
        
        return True
    
    except subprocess.CalledProcessError as e:
        logger.error(f"Git command failed: {type(e).__name__}")
        return False
    except Exception as e:
        logger.error(f"Error pushing to GitHub: {type(e).__name__}")
        return False

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
        logger.info("")
        datafeedwatch_products = fetch_datafeedwatch_products()
        
        if not datafeedwatch_products:
            logger.error("No products fetched from datafeedwatch. Aborting.")
            return False
        
        logger.info("")
        products = fetch_all_products()
        
        if not products:
            logger.error("No products fetched from Neto. Aborting.")
            return False
        
        logger.info("")
        feed_entries = build_category_feed(products, datafeedwatch_products)
        
        if not feed_entries:
            logger.error("No feed entries built. Aborting.")
            return False
        
        logger.info("")
        success, feed_content = upload_supplementary_feed(feed_entries)
        
        if not success:
            logger.error("=" * 70)
