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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION - Read from environment variables
# ============================================================================

NETO_API_KEY = os.getenv('NETO_API_KEY', '')
NETO_CUSTOMER_ID = os.getenv('NETO_CUSTOMER_ID', '')
GOOGLE_MERCHANT_ID = os.getenv('GOOGLE_MERCHANT_ID', '5485680660')
GOOGLE_CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON', '')

# Validate environment variables
if not NETO_API_KEY:
    raise ValueError("NETO_API_KEY not set in environment variables")
if not NETO_CUSTOMER_ID:
    raise ValueError("NETO_CUSTOMER_ID not set in environment variables")
if not GOOGLE_CREDENTIALS_JSON:
    raise ValueError("GOOGLE_CREDENTIALS_JSON not set in environment variables")

# Parse Google credentials
try:
    GOOGLE_CREDS = json.loads(GOOGLE_CREDENTIALS_JSON)
except json.JSONDecodeError as e:
    raise ValueError(f"GOOGLE_CREDENTIALS_JSON is not valid JSON: {e}")

# ============================================================================
# NETO API FUNCTIONS
# ============================================================================

def neto_api_call(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Make authenticated call to Neto API"""
    url = "https://www.thebbqstore.com.au/do/WS/NetoAPI"
    
    headers = {
        'NETOAPI_ACTION': action,
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

def extract_categories(item: Dict[str, Any]) -> List[str]:
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

def build_category_feed(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build feed entries with SKU and product_type (categories)
    
    Format: [{"id": "SKU123", "product_type": "Cat1, Cat2, Cat3"}, ...]
    """
    feed_entries = []
    products_with_categories = 0
    products_without_categories = 0
    
    for product in products:
        sku = product.get("SKU", "").strip()
        if not sku:
            continue
        
        categories = extract_categories(product)
        
        if categories:
            products_with_categories += 1
            product_type = ", ".join(categories)
        else:
            products_without_categories += 1
            product_type = ""
        
        feed_entries.append({
            "id": sku,
            "product_type": product_type
        })
    
    logger.info(f"Products with categories: {products_with_categories}")
    logger.info(f"Products without categories: {products_without_categories}")
    logger.info(f"Total feed entries: {len(feed_entries)}")
    
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
    """Upload supplementary feed to Google Merchant Center"""
    
    logger.info("Getting Google API access token...")
    access_token = get_google_access_token()
    
    # Build the feed content
    feed_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
    feed_content += '<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">\n'
    feed_content += '<channel>\n'
    
    for entry in feed_entries:
        sku = entry['id']
        product_type = entry['product_type']
        
        feed_content += '  <item>\n'
        feed_content += f'    <g:id>{escape_xml(sku)}</g:id>\n'
        
        if product_type:
            feed_content += f'    <g:product_type>{escape_xml(product_type)}</g:product_type>\n'
        
        feed_content += '  </item>\n'
    
    feed_content += '</channel>\n'
    feed_content += '</rss>\n'
    
    # Save feed to file for logging/debugging
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    feed_filename = f"/tmp/neto_gmc_feed_{timestamp}.xml"
    
    try:
        with open(feed_filename, 'w') as f:
            f.write(feed_content)
        logger.info(f"Feed saved to {feed_filename}")
    except Exception as e:
        logger.warning(f"Could not save feed file: {e}")
    
    # Upload to Google Merchant Center
    logger.info(f"Uploading supplementary feed to GMC (Merchant ID: {GOOGLE_MERCHANT_ID})...")
    
    url = f"https://www.googleapis.com/content/v2.1/{GOOGLE_MERCHANT_ID}/datafeeds"
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/xml',
    }
    
    try:
        # First, try to get existing supplementary feed
        existing_feeds = get_existing_feeds(access_token)
        feed_id = existing_feeds.get('neto_categories')
        
        if feed_id:
            logger.info(f"Updating existing feed {feed_id}...")
            update_url = f"{url}/{feed_id}"
            response = requests.put(
                update_url,
                data=feed_content,
                headers=headers,
                timeout=60
            )
        else:
            logger.info("Creating new supplementary feed...")
            response = requests.post(
                url,
                data=feed_content,
                headers=headers,
                timeout=60
            )
        
        response.raise_for_status()
        logger.info(f"✓ Feed uploaded successfully")
        logger.info(f"Response status: {response.status_code}")
        
        return True
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to upload feed to GMC: {e}")
        if hasattr(e.response, 'text'):
            logger.error(f"Response: {e.response.text}")
        raise

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
        # Fetch products from Neto
        products = fetch_all_products()
        
        if not products:
            logger.error("No products fetched from Neto. Aborting.")
            return False
        
        # Build feed
        feed_entries = build_category_feed(products)
        
        if not feed_entries:
            logger.error("No feed entries built. Aborting.")
            return False
        
        # Upload to GMC
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
