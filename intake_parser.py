"""
Print Junkie AZ — Intake Parser
Parses raw text (SMS, voice transcripts, pasted conversations) into structured quote data.
Domain-specific validation enforces required fields per product type.
"""

import re
import json
from typing import Optional

# ─── DOMAIN CHECKLISTS ─────────────────────────────────────────────────────────

APPAREL_KEYWORDS = {"shirt", "shirts", "hoodie", "hoodies", "hat", "hats", "tee", "tees",
                    "jersey", "jerseys", "tank", "jacket", "jackets", "polo"}
BANNER_KEYWORDS = {"banner", "banners", "sign", "signs", "flag", "flags", "vinyl", "backdrop"}
CARD_KEYWORDS = {"card", "cards", "business card", "business cards", "flyer", "flyers", "brochure"}
LABEL_KEYWORDS = {"label", "labels", "sticker", "stickers", "uv dtf", "gang sheet", "gang sheets"}

SIZE_KEYWORDS = {"small": "S", "medium": "M", "large": "L", "xl": "XL", "xxl": "2XL", "2xl": "2XL",
                 "s": "S", "m": "M", "l": "L"}
COLOR_KEYWORDS = {"black", "white", "red", "blue", "green", "navy", "grey", "gray", "pink",
                  "gold", "yellow", "orange", "purple", "brown", "teal", "maroon", "olive"}

# ─── EXTRACTION HELPERS ────────────────────────────────────────────────────────

def extract_customer(text: str) -> Optional[str]:
    """Extract customer name: 'Bill wants...', 'for Roy', 'customer: Mo'."""
    patterns = [
        r"(?:customer|client)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+wants",
        r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+needs",
        r"(?:for|from)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            if name.lower() not in {"the", "a", "an", "i", "we", "he", "she", "they"}:
                return name
    return None


def extract_quantity(text: str) -> int:
    """Extract quantity. Default 1 if not found."""
    patterns = [
        r"(\d+)\s*(?:x\s+)?(?:pcs?|pieces|units?|sheets?)",
        r"(\d+)\s+(?:black|white|red|blue|green|navy|grey|gray|pink|gold|yellow|orange|purple|brown|teal|maroon|olive)\s+\w+",
        r"(?:need|want|order|needs|wants)\s+(\d+)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return int(m.group(1))
    # Fallback: standalone number
    m = re.search(r'\b(\d+)\b', text)
    return int(m.group(1)) if m else 1


def extract_deadline(text: str) -> Optional[str]:
    """Extract deadline: 'by Friday', 'next week', 'ASAP'."""
    patterns = [
        r"(?:by|before|need(?:s|ed)? (?:by|before))\s+(next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|month)",
        r"(?:asap|urgent|rush|emergency)",
        r"deadline[:\s]+(.+?)(?:\.|$)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return None


def extract_color(text: str) -> Optional[str]:
    """First color mentioned."""
    for color in COLOR_KEYWORDS:
        if re.search(rf"\b{color}\b", text, re.IGNORECASE):
            return color.capitalize()
    return None


def extract_sizes(text: str) -> dict:
    """Extract size breakdown: '20M 15L 5XL' or '20 medium'."""
    sizes = {}
    for m in re.finditer(r"(\d+)\s*(?:x\s*)?(s|m|l|xl|xxl|2xl)\b", text, re.IGNORECASE):
        qty = int(m.group(1))
        size = SIZE_KEYWORDS.get(m.group(2).lower(), m.group(2).upper())
        sizes[size] = sizes.get(size, 0) + qty
    for m in re.finditer(r"(\d+)\s*(?:x\s*)?(small|medium|large)\b", text, re.IGNORECASE):
        qty = int(m.group(1))
        size = SIZE_KEYWORDS.get(m.group(2).lower(), m.group(2).upper())
        sizes[size] = sizes.get(size, 0) + qty
    return sizes


def detect_product_type(text: str) -> str:
    """Detect product category from keywords."""
    t = text.lower()
    for kw in APPAREL_KEYWORDS:
        if kw in t:
            return "apparel"
    for kw in BANNER_KEYWORDS:
        if kw in t:
            return "banner"
    for kw in CARD_KEYWORDS:
        if kw in t:
            return "business_card"
    for kw in LABEL_KEYWORDS:
        if kw in t:
            return "label"
    return "generic"


def extract_product_name(text: str, product_type: str) -> str:
    """Extract specific product name."""
    for kw_list in [APPAREL_KEYWORDS, BANNER_KEYWORDS, CARD_KEYWORDS, LABEL_KEYWORDS]:
        for kw in kw_list:
            if kw in text.lower():
                # Return singular, capitalized
                name = kw.rstrip("s").capitalize() if kw.endswith("s") else kw.capitalize()
                return name
    fallback = {"apparel": "Apparel", "banner": "Banner", "business_card": "Business Cards",
                "label": "Labels/Stickers", "generic": "Custom Product"}
    return fallback.get(product_type, "Custom Product")


def extract_price(text: str) -> Optional[float]:
    """Extract price: '$25 each', '$500 total'."""
    m = re.search(r"\$(\d+(?:\.\d{2})?)\s*(?:each|per|ea|/pc|/piece|total)?", text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def extract_special_requests(text: str) -> list:
    """Extract special requests: 'logo on back', 'rush', 'matte finish'."""
    found = []
    patterns = [
        r"logo\s+(?:on|in)\s+(?:the\s+)?(?:back|front|sleeve|left|right|both)",
        r"(?:rush|expedited|urgent)\s+(?:order|delivery)?",
        r"(?:double|single)[- ]sided",
        r"(?:matte|gloss|satin)\s+(?:finish|coat)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            found.append(m.group(0))
    return found


# ─── VALIDATION ─────────────────────────────────────────────────────────────────

def validate_item(item: dict) -> tuple:
    """Validate against domain checklists. Returns (is_valid, missing, question)."""
    pt = item.get("product_type", "generic")
    variants = item.get("variants", {})
    missing = []
    question = None

    if pt == "apparel":
        if not variants.get("sizes"):
            missing.append("size_matrix")
            question = f"What sizes for the {item['product_name']}? (e.g., 20M, 15L, 5XL)"
        if not variants.get("color"):
            missing.append("color")
            if not question:
                question = f"What color for the {item['product_name']}?"

    elif pt == "banner":
        if not variants.get("dimensions"):
            missing.append("dimensions")
            question = f"What size for the {item['product_name']}? (e.g., 3x6 ft)"
        if not variants.get("finishing"):
            missing.append("finishing_options")
            if not question:
                question = "Hems/grommets? Indoor or outdoor?"

    elif pt == "business_card":
        if not variants.get("paper_weight"):
            missing.append("paper_weight")
            question = "What paper weight? (e.g., 16pt, 14pt)"
        if not variants.get("finish"):
            missing.append("finish")
            if not question:
                question = "Matte or Gloss finish?"
        if not variants.get("corners"):
            missing.append("corners")
            if not question:
                question = "Standard or Rounded corners?"

    elif pt == "label":
        if not variants.get("size") and not variants.get("dimensions"):
            missing.append("size")
            question = f"What size/shape for the {item['product_name']}?"

    return len(missing) == 0, missing, question


# ─── MAIN PARSER ────────────────────────────────────────────────────────────────

def parse_intake(raw_text: str) -> dict:
    """
    Parse raw text into structured quote data.
    Returns:
      {"status": "ready", "customer": ..., "items": [...]} — ready to create quote
      {"status": "needs_clarification", "parsed_so_far": {...}, "missing": [...], "question": "..."}
    """
    text = raw_text.strip()
    if not text:
        return {"status": "error", "error": "Empty input"}

    customer = extract_customer(text)
    deadline = extract_deadline(text)
    price_hint = extract_price(text)
    special_requests = extract_special_requests(text)

    # Split into logical segments
    segments = re.split(r"(?:\n|(?:(?:^|\s)and\s+(?=\d|a\s|an\s|the\s)))", text, flags=re.IGNORECASE)
    segments = [s.strip() for s in segments if s.strip()]
    if not segments:
        segments = [text]

    items = []
    all_missing = []
    first_question = None

    for seg in segments:
        product_type = detect_product_type(seg)
        product_name = extract_product_name(seg, product_type)
        quantity = extract_quantity(seg)
        color = extract_color(seg)
        sizes = extract_sizes(seg)
        seg_price = extract_price(seg)

        variants = {}
        if color:
            variants["color"] = color
        if sizes:
            variants["sizes"] = sizes

        # Banner-specific
        if product_type == "banner":
            dim = re.search(r"(\d+)\s*[xX×]\s*(\d+)\s*(ft|feet|in|inches)?", seg)
            if dim:
                unit = dim.group(3) or "ft"
                variants["dimensions"] = f"{dim.group(1)}x{dim.group(2)} {unit}"
            if re.search(r"grommet", seg, re.IGNORECASE):
                variants["finishing"] = "grommets"
            if re.search(r"hem", seg, re.IGNORECASE):
                variants["finishing"] = variants.get("finishing", "") + " hems"
            if re.search(r"indoor|outdoor", seg, re.IGNORECASE):
                variants["placement"] = re.search(r"(indoor|outdoor)", seg, re.IGNORECASE).group(1).lower()

        # Business card-specific
        elif product_type == "business_card":
            pw = re.search(r"(\d+)\s*pt", seg, re.IGNORECASE)
            if pw:
                variants["paper_weight"] = pw.group(0)
            if re.search(r"matte", seg, re.IGNORECASE):
                variants["finish"] = "Matte"
            elif re.search(r"gloss", seg, re.IGNORECASE):
                variants["finish"] = "Gloss"
            if re.search(r"rounded", seg, re.IGNORECASE):
                variants["corners"] = "Rounded"
            elif re.search(r"standard|square", seg, re.IGNORECASE):
                variants["corners"] = "Standard"

        item = {
            "product_name": product_name,
            "product_type": product_type,
            "quantity": quantity,
            "quoted_price": seg_price or price_hint,
            "variants": variants,
        }

        is_valid, missing, question = validate_item(item)
        if not is_valid:
            all_missing.extend(missing)
            if first_question is None:
                first_question = question

        item.pop("product_type", None)
        items.append(item)

    if all_missing:
        return {
            "status": "needs_clarification",
            "parsed_so_far": {
                "customer": customer,
                "items": items,
                "deadline": deadline,
                "special_requests": special_requests,
            },
            "missing": all_missing,
            "question": first_question,
        }

    return {
        "status": "ready",
        "customer": customer,
        "items": items,
        "deadline": deadline,
        "special_requests": special_requests,
        "notes": raw_text[:500],
    }


# ─── CLI TEST ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        "Bill wants 40 black shirts, logo on back, needs by next Friday",
        "Terpy needs UV DTF gang sheets, 5 sheets",
        "North Rim wants 100 business cards matte finish",
        "Roy wants a 3x6 ft banner with grommets",
        "Mo needs 50 hoodies",
        "20M 15L 5XL black shirts for Bill, $25 each, by Friday",
    ]
    for t in tests:
        print(f"\nInput: {t}")
        print(json.dumps(parse_intake(t), indent=2))
        print("-" * 50)
