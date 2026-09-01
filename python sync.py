import os
import json
import re
import unicodedata
from datetime import datetime
import requests

TY_BASE = "https://apigw.trendyol.com"
HB_BASE = "https://mpop-sit.hepsiburada.com"
TY_PAGE_SIZE = 100
HB_PAGE_SIZE = 1000
TIMEOUT = 120

TY_SUPPLIER_ID = os.getenv("TY_SUPPLIER_ID", "").strip()
TY_API_KEY = os.getenv("TY_API_KEY", "").strip()
TY_API_SECRET = os.getenv("TY_API_SECRET", "").strip()
HB_MERCHANT_ID = os.getenv("HB_MERCHANT_ID", "").strip()
HB_SECRET_KEY = os.getenv("HB_SECRET_KEY", "").strip()
HB_USERNAME = os.getenv("HB_USERNAME", "").strip()


def log(msg):
    print(f"[{datetime.now():%d.%m.%Y %H:%M:%S}] {msg}", flush=True)


def require_secrets():
    required = {
        "TY_SUPPLIER_ID": TY_SUPPLIER_ID,
        "TY_API_KEY": TY_API_KEY,
        "TY_API_SECRET": TY_API_SECRET,
        "HB_MERCHANT_ID": HB_MERCHANT_ID,
        "HB_SECRET_KEY": HB_SECRET_KEY,
        "HB_USERNAME": HB_USERNAME,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError("Eksik GitHub Secret: " + ", ".join(missing))


def safe(v):
    return "" if v is None else str(v).strip()


def norm(v):
    s = safe(v).lower().translate(str.maketrans({"ı":"i","ş":"s","ğ":"g","ü":"u","ö":"o","ç":"c"}))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def json_or_raise(r, name):
    if r.status_code >= 400:
        raise RuntimeError(f"{name} HTTP {r.status_code}: {r.text[:3000]}")
    try:
        return r.json()
    except ValueError as e:
        raise RuntimeError(f"{name} JSON cevabı vermedi: {r.text[:2000]}") from e


def get_trendyol_products():
    url = f"{TY_BASE}/integration/product/sellers/{TY_SUPPLIER_ID}/products/approved"
    headers = {"User-Agent": f"{TY_SUPPLIER_ID} - SelfIntegration", "Accept": "application/json"}
    products = []
    page = 0
    while True:
        r = requests.get(url, headers=headers, auth=(TY_API_KEY, TY_API_SECRET),
                         params={"page": page, "size": TY_PAGE_SIZE}, timeout=TIMEOUT)
        log(f"📡 Trendyol Sayfa {page + 1} | HTTP: {r.status_code}")
        data = json_or_raise(r, "Trendyol")
        content = data.get("content") or []
        if not content:
            break
        for p in content:
            variants = p.get("variants") or [{}]
            for v in variants:
                barcode = safe(v.get("barcode") or p.get("barcode"))
                if not barcode:
                    continue
                price = v.get("price") or {}
                stock = v.get("stock") or {}
                images = []
                for im in p.get("images") or []:
                    u = im.get("url") if isinstance(im, dict) else im
                    if not u and isinstance(im, dict):
                        u = im.get("imageUrl")
                    if u:
                        images.append(safe(u))
                brand = p.get("brand")
                if isinstance(brand, dict):
                    brand = brand.get("name", "")
                brand = safe(brand or p.get("brandName") or "Dolunay Takı")
                products.append({
                    "barcode": barcode,
                    "title": safe(p.get("title")),
                    "description": safe(p.get("description")),
                    "price": price.get("salePrice", price.get("listPrice", p.get("salePrice", 0))),
                    "stock": stock.get("quantity", v.get("quantity", p.get("quantity", 0))),
                    "images": images[:10],
                    "category": safe(p.get("categoryName") or p.get("category")),
                    "productMainId": safe(p.get("productMainId")),
                    "productCode": safe(p.get("productCode")),
                    "stockCode": safe(v.get("stockCode") or p.get("stockCode")),
                    "brand": brand,
                    "attributes": p.get("attributes") or [],
                    "variantAttributes": v.get("attributes") or [],
                })
        log(f"📦 Sayfa {page + 1}: {len(content)} ana ürün | Varyant toplam: {len(products)}")
        total_pages = data.get("totalPages")
        if total_pages is not None and page + 1 >= int(total_pages):
            break
        if total_pages is None and len(content) < TY_PAGE_SIZE:
            break
        page += 1
    return products


def get_hb_categories():
    url = f"{HB_BASE}/product/api/categories/get-all-categories"
    headers = {"User-Agent": HB_USERNAME, "Accept": "application/json"}
    categories, page = [], 0
    while True:
        r = requests.get(url, headers=headers, auth=(HB_MERCHANT_ID, HB_SECRET_KEY),
                         params={"leaf":"true","status":"ACTIVE","available":"true",
                                 "page":page,"size":HB_PAGE_SIZE}, timeout=TIMEOUT)
        log(f"📂 HB kategori sayfa {page + 1} | HTTP: {r.status_code}")
        data = json_or_raise(r, "Hepsiburada kategori")
        if isinstance(data, list):
            items, total_pages = data, None
        else:
            items = data.get("data") or data.get("content") or data.get("categories") or []
            total_pages = data.get("totalPages")
        items = [x for x in items if isinstance(x, dict)]
        categories.extend(items)
        log(f"📂 Kategori: {len(items)} | Toplam: {len(categories)}")
        if total_pages is not None and page + 1 >= int(total_pages):
            break
        if total_pages is None and len(items) < HB_PAGE_SIZE:
            break
        page += 1
    return [c for c in categories if c.get("leaf") is True and safe(c.get("status")).upper() == "ACTIVE" and c.get("available") is True]


def category_text(c):
    paths = c.get("paths") or []
    if not isinstance(paths, list):
        paths = []
    return norm(" ".join([safe(c.get("name")), safe(c.get("displayName"))] + [safe(x) for x in paths]))


def find_category(product, categories):
    source = norm(f"{product.get('category')} {product.get('title')}")
    if "bileklik" in source or "kelepce" in source:
        keys = ["bileklik", "kelepce", "sahmeran"]
    elif "kolye" in source:
        keys = ["kolye"]
    elif "kupe" in source:
        keys = ["kupe"]
    elif "yuzuk" in source:
        keys = ["yuzuk"]
    elif "piercing" in source:
        keys = ["piercing"]
    elif "sahmeran" in source:
        keys = ["sahmeran", "bileklik"]
    elif "halhal" in source:
        keys = ["halhal"]
    else:
        keys = []
    best, best_score = None, 0
    words = set(norm(product.get("category")).split())
    for c in categories:
        text = category_text(c)
        score = sum(100 for k in keys if k in text)
        score += sum(20 for w in words if len(w) >= 4 and w in text)
        if score > best_score:
            best_score, best = score, c
    return best


def get_hb_attributes(category_id):
    url = f"{HB_BASE}/product/api/categories/{category_id}/attributes"
    r = requests.get(url, headers={"User-Agent": HB_USERNAME, "Accept": "application/json"},
                     auth=(HB_MERCHANT_ID, HB_SECRET_KEY), params={"version": 2}, timeout=TIMEOUT)
    log(f"📋 Kategori {category_id} özellikleri | HTTP: {r.status_code}")
    if r.status_code != 200:
        return []
    data = r.json()
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "content", "attributes"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def build_product(p, c, hb_attrs):
    price = p.get("price", 0)
    try:
        price = f"{float(price):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        price = "0,00"
    try:
        stock = str(int(float(p.get("stock", 0))))
    except (TypeError, ValueError):
        stock = "0"
    sku = p.get("stockCode") or p.get("productCode") or p.get("barcode")
    attrs = {
        "merchantSku": safe(sku),
        "VaryantGroupID": p.get("productMainId") or safe(sku),
        "Barcode": p.get("barcode"),
        "UrunAdi": p.get("title"),
        "UrunAciklamasi": p.get("description"),
        "Marka": p.get("brand") or "Dolunay Takı",
        "GarantiSuresi": 0,
        "kg": "1",
        "price": price,
        "stock": stock,
    }
    for i, image in enumerate(p.get("images") or [], start=1):
        attrs[f"Image{i}"] = image
    ty_attrs = {}
    for a in p.get("attributes") or []:
        if not isinstance(a, dict):
            continue
        n = a.get("attributeName") or a.get("name")
        v = a.get("attributeValue") or a.get("value")
        if n and v:
            ty_attrs[norm(n)] = safe(v)
    for a in hb_attrs:
        n = a.get("name") or a.get("isim")
        if n and norm(n) in ty_attrs and n not in attrs:
            attrs[n] = ty_attrs[norm(n)]
    for a in p.get("variantAttributes") or []:
        if not isinstance(a, dict):
            continue
        n = norm(a.get("attributeName") or a.get("name") or "")
        v = safe(a.get("attributeValue") or a.get("value"))
        if not v:
            continue
        if "renk" in n:
            attrs["renk_variant_property"] = v
        elif "beden" in n:
            attrs["beden_variant_property"] = v
        elif "ebat" in n:
            attrs["ebatlar_variant_property"] = v
    return {"categoryId": c.get("categoryId"), "merchant": HB_MERCHANT_ID, "attributes": attrs}


def send_products(products):
    if not products:
        raise RuntimeError("Gönderilecek ürün yok.")
    url = f"{HB_BASE}/product/api/products/import"
    filename = f"hepsiburada_import_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    log(f"📄 {len(products)} ürünlük JSON oluşturuldu: {filename}")
    with open(filename, "rb") as f:
        r = requests.post(url,
                          headers={"User-Agent": HB_USERNAME, "Accept": "application/json"},
                          auth=(HB_MERCHANT_ID, HB_SECRET_KEY),
                          files={"file": (filename, f, "application/json")}, timeout=180)
    log(f"📡 HB ürün import | HTTP: {r.status_code}")
    print("HEPSİBURADA CEVABI:")
    print(r.text[:10000])
    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f"HB import başarısız: HTTP {r.status_code}")


def main():
    require_secrets()
    print("=" * 70)
    print("TRENDYOL → HEPSİBURADA SENKRONİZASYON")
    print("=" * 70)

    # Her GitHub Actions çalışmasında tüm ürünler kontrol edilir.
    # Böylece GitHub runner'ın geçici diskinde cache tutmaya gerek kalmaz.
    products = get_trendyol_products()
    log(f"✅ Trendyol toplam varyant: {len(products)}")
    if not products:
        raise RuntimeError("Trendyol'dan ürün gelmedi.")

    categories = get_hb_categories()
    log(f"✅ HB aktif/ürün eklenebilir kategori: {len(categories)}")
    if not categories:
        raise RuntimeError("Hepsiburada'dan aktif kategori gelmedi.")

    result = []
    unmatched = []
    attr_cache = {}

    for i, product in enumerate(products, 1):
        log(f"🔄 [{i}/{len(products)}] {product['title']}")
        category = find_category(product, categories)
        if not category:
            unmatched.append(product)
            log(f"⚠️ Kategori eşleşmedi: {product.get('category')}")
            continue
        cid = category.get("categoryId")
        log(f"   → HB kategori: {category.get('displayName')} ({cid})")
        if cid not in attr_cache:
            attr_cache[cid] = get_hb_attributes(cid)
        result.append(build_product(product, category, attr_cache[cid]))

    log(f"📊 Hazırlanan: {len(result)} | Eşleşmeyen: {len(unmatched)}")
    if unmatched:
        with open("kategori_eslesmeyenler.json", "w", encoding="utf-8") as f:
            json.dump(unmatched, f, ensure_ascii=False, indent=2)
    if result:
        send_products(result)
    else:
        raise RuntimeError("Hiç ürün hazırlanamadı; Hepsiburada'ya gönderim yapılmadı.")
    log("✅ SENKRONİZASYON TAMAMLANDI")


if __name__ == "__main__":
    main()
