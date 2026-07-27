import os
import re
import io
import requests
import qrcode
import pdfplumber
from datetime import datetime
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)
CORS(app)

# ============================================================
# CONFIG — FRONT CARD
# ============================================================
FRONT_CARD_TEMPLATE_URL = "https://i.ibb.co/V6vxpJB/20260727-154752.jpg"
AGRISTACK_URL = "https://www.upfr.agristack.gov.in/farmer-registry-up/"

TEMPLATE_W, TEMPLATE_H = 1559, 1009

PHOTO_BOX      = (141, 274, 483, 709)
QR_BOX         = (1166, 459, 1347, 717)
FARMER_ID_BOX  = (518, 747, 1038, 897)

CONTENT_X0 = 523
CONTENT_X1 = 1140

NAME_ROW_TOP = 300
NAME_ROW_HEIGHT = 85
ROW_GAP = 5
LABEL_ROW_HEIGHT = 75

AADHAAR_ROW_GAP = 3

PHOTO_PADDING_LEFT = 18
PHOTO_PADDING_RIGHT = 18
PHOTO_PADDING_TOP = 37
PHOTO_PADDING_BOTTOM = 20

QR_PADDING = -7
QR_SHIFT_X = 27
QR_SHIFT_Y = 0

NAME_FONT_SIZE = 55
LABEL_FONT_SIZE = 37
FARMER_ID_FONT_SIZE = 63

FONT_REGULAR_PATH = "Poppins-Regular.ttf"
FONT_BOLD_PATH = "Poppins-Bold.ttf"

# ============================================================
# CONFIG — BACK CARD
# ============================================================
BACK_CARD_TEMPLATE_URL = "https://i.ibb.co/Q7VQkx3P/Chat-GPT-Image-Jul-21-2026-02-28-42-PM.png"

BACK_TEMPLATE_W, BACK_TEMPLATE_H = 1537, 1023

BACK_CONTENT_BOX = (70, 110, 1467, 913)

ADDRESS_LEFT_PADDING = 60

ADDRESS_ROW_HEIGHT = 90
ADDRESS_FONT_SIZE = 42

TABLE_TOP_GAP = 30

MIN_TABLE_FONT = 17
MAX_TABLE_FONT = 27
ROW_PADDING_RATIO = 1.9

# ============================================================
# CONFIG — PRINT-READY A4 SHEET
# Ayushman page ke jaisa hi fixed pixel-math layout, 300 DPI, portrait A4
# ============================================================
A4_CANVAS_W = 2480   # A4 @ 300 DPI, portrait width
A4_CANVAS_H = 3508   # A4 @ 300 DPI, portrait height

CARD_W = 1016        # Standard CR80 card width @ 300 DPI (86mm)
CARD_H = 638         # Standard CR80 card height @ 300 DPI (54mm)

PRINT_SCALE = 1.10   # Card ko thoda bada karke print karna (jaisa Ayushman page mein hai)

START_Y = 150        # Upar se margin
GAP_X = 100           # Front aur Back card ke beech ka gap
GAP_Y = 120           # (agar future mein multiple rows chahiye ho, abhi ek hi row hai)


def get_font(bold, size):
    path = FONT_BOLD_PATH if bold else FONT_REGULAR_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except Exception:
            return ImageFont.load_default()


# ============================================================
# KNOWN NAME LOOKUPS
# ============================================================
INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
    "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim",
    "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
    "West Bengal", "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir",
    "Ladakh", "Lakshadweep", "Puducherry"
]

UP_DISTRICTS = [
    "Agra", "Aligarh", "Ambedkar Nagar", "Amethi", "Amroha", "Auraiya",
    "Ayodhya", "Azamgarh", "Baghpat", "Bahraich", "Ballia", "Balrampur",
    "Banda", "Barabanki", "Bareilly", "Basti", "Bhadohi", "Bijnor",
    "Budaun", "Bulandshahr", "Chandauli", "Chitrakoot", "Deoria", "Etah",
    "Etawah", "Farrukhabad", "Fatehpur", "Firozabad", "Gautam Buddha Nagar",
    "Ghaziabad", "Ghazipur", "Gonda", "Gorakhpur", "Hamirpur", "Hapur",
    "Hardoi", "Hathras", "Jalaun", "Jaunpur", "Jhansi", "Kannauj",
    "Kanpur Dehat", "Kanpur Nagar", "Kasganj", "Kaushambi", "Kheri",
    "Kushinagar", "Lalitpur", "Lucknow", "Maharajganj", "Mahoba",
    "Mainpuri", "Mathura", "Mau", "Meerut", "Mirzapur", "Moradabad",
    "Muzaffarnagar", "Pilibhit", "Pratapgarh", "Prayagraj", "Raebareli",
    "Rampur", "Saharanpur", "Sambhal", "Sant Kabir Nagar", "Shahjahanpur",
    "Shamli", "Shravasti", "Siddharthnagar", "Sitapur", "Sonbhadra",
    "Sultanpur", "Unnao", "Varanasi"
]


def _build_lookup(names):
    lookup = {}
    for name in names:
        key = re.sub(r"\s+", "", name).upper()
        lookup[key] = name.upper()
    return lookup


STATE_LOOKUP = _build_lookup(INDIAN_STATES)
DISTRICT_LOOKUP = _build_lookup(UP_DISTRICTS)


def clean_nospace(value):
    if value is None:
        return ""
    v = value.replace("\n", "")
    v = re.sub(r"\s+", " ", v).strip()
    v = v.rstrip(",").strip()
    return v


def clean_and_match(value, lookup):
    if value is None:
        return ""
    concatenated = value.replace("\n", "")
    concatenated = re.sub(r"\s+", "", concatenated).strip().rstrip(",")
    key = concatenated.upper()

    if key in lookup:
        return lookup[key]

    fallback = value.replace("\n", " ")
    fallback = re.sub(r"\s+", " ", fallback).strip().rstrip(",").strip()
    return fallback


def row_looks_like_land_row(row):
    if not row or len(row) < 12:
        return False
    key = re.sub(r"\s+", "", (row[0] or "")).upper()
    return key in STATE_LOOKUP


# ============================================================
# PDF SE ENGLISH FARMER NAME NIKALNA
# ============================================================
def extract_english_name(pdf_bytes):
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = pdf.pages[0].extract_text() or ""
        m = re.search(r"Farmer Name as per Aadhaar in English\s+(.+?)\s+Farmer.s Name in Local Language", text)
        return m.group(1).strip() if m else "N/A"


def get_first_name(full_name):
    if not full_name or full_name == "N/A":
        return "N/A"
    return full_name.strip().split()[0]


# ============================================================
# PDF SE FRONT DATA NIKALNA
# ============================================================
def format_dob(dob_str):
    if not dob_str:
        return dob_str
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", dob_str.strip())
    if not m:
        return dob_str

    day, month, year = m.group(1), m.group(2), m.group(3)
    day = day.zfill(2)
    month = month.zfill(2)

    if len(year) == 2:
        yy = int(year)
        current_yy = int(str(datetime.now().year)[-2:])
        century = 1900 if yy > current_yy else 2000
        year = str(century + yy)

    return f"{day}/{month}/{year}"


def extract_farmer_data(pdf_bytes):
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""

        def find(pattern, default="N/A"):
            m = re.search(pattern, text)
            return m.group(1).strip() if m else default

        name = find(r"Farmer Name as per Aadhaar in English\s+(.+?)\s+Farmer.s Name in Local Language")
        dob = find(r"Date of Birth\s+([\d/]+)")
        gender = find(r"Gender\s+(Male|Female|Transgender)")
        caste = find(r"Caste Category\s+([A-Za-z]+)")
        mobile = find(r"Mobile Number\s+(\d{6,15})")

        dob = format_dob(dob)

        photo_img = None
        if page.images:
            biggest = max(
                page.images,
                key=lambda im: (im["x1"] - im["x0"]) * (im["bottom"] - im["top"])
            )
            bbox = (biggest["x0"], biggest["top"], biggest["x1"], biggest["bottom"])
            cropped = page.crop(bbox).to_image(resolution=400)
            photo_img = cropped.original.convert("RGB")

        return {
            "name": name,
            "dob": dob,
            "gender": gender,
            "caste": caste,
            "mobile": mobile,
            "photo": photo_img,
        }


# ============================================================
# PDF SE BACK DATA NIKALNA — poora PDF scan
# ============================================================
def extract_back_data(pdf_bytes):
    address = "N/A"
    land_rows = []

    english_name = extract_english_name(pdf_bytes)
    owner_first_name = get_first_name(english_name)

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page0_text = pdf.pages[0].extract_text() or ""
        m = re.search(r"Address In English\s+(.+?)\s+Address In Local Language", page0_text)
        if m:
            address = m.group(1).strip()

        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue

                header = [clean_nospace(c).lower() for c in table[0] if c is not None]
                header_joined = " ".join(header)
                is_header_table = ("owner" in header_joined) and ("extent" in header_joined)

                candidate_rows = table[1:] if is_header_table else table

                for row in candidate_rows:
                    if not row_looks_like_land_row(row):
                        continue

                    state = clean_and_match(row[0], STATE_LOOKUP)
                    district = clean_and_match(row[1], DISTRICT_LOOKUP)

                    s_no_raw = clean_nospace(row[4])
                    s_no_match = re.match(r"(\d+)", s_no_raw)
                    s_no = s_no_match.group(1) if s_no_match else s_no_raw

                    owner = owner_first_name

                    total_area = clean_nospace(row[10])
                    assigned_area = clean_nospace(row[11])

                    land_rows.append({
                        "state": state,
                        "district": district,
                        "s_no": s_no,
                        "owner": owner,
                        "total_area": total_area,
                        "assigned_area": assigned_area,
                    })

    return {"address": address, "land_rows": land_rows}


# ============================================================
# IMAGE HELPERS
# ============================================================
def shrink_box_asym(box, left, top, right, bottom):
    x0, y0, x1, y1 = box
    return (x0 + left, y0 + top, x1 - right, y1 - bottom)


def cover_fit(img, box_w, box_h):
    img_ratio = img.width / img.height
    box_ratio = box_w / box_h

    if img_ratio > box_ratio:
        new_h = box_h
        new_w = int(new_h * img_ratio)
    else:
        new_w = box_w
        new_h = int(new_w / img_ratio)

    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - box_w) // 2
    top = (new_h - box_h) // 2
    return resized.crop((left, top, left + box_w, top + box_h))


def draw_left_text(draw, box, text, size, bold=False, fill="#1A2238"):
    x0, y0, x1, y1 = box
    box_h = y1 - y0
    font = get_font(bold, size)
    bbox = draw.textbbox((0, 0), text, font=font)
    th = bbox[3] - bbox[1]
    ty = y0 + (box_h - th) // 2 - bbox[1]
    draw.text((x0, ty), text, font=font, fill=fill)


def draw_label_value(draw, box, label, value, label_size=90, value_gap=16):
    x0, y0, x1, y1 = box
    box_h = y1 - y0

    font_label = get_font(True, label_size)
    font_value = get_font(False, label_size)

    label_bbox = draw.textbbox((0, 0), label, font=font_label)
    label_w = label_bbox[2] - label_bbox[0]
    label_h = label_bbox[3] - label_bbox[1]

    text_y = y0 + (box_h - label_h) // 2 - label_bbox[1]

    draw.text((x0, text_y), label, font=font_label, fill="#1A2238")
    draw.text((x0 + label_w + value_gap, text_y), str(value), font=font_value, fill="#1A2238")


def draw_text_in_box(draw, box, text, font, fill="#1A2238"):
    x0, y0, x1, y1 = box
    box_w, box_h = x1 - x0, y1 - y0
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = x0 + max((box_w - tw) // 2, 4)
    ty = y0 + (box_h - th) // 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=fill)


def draw_centered_text(draw, box, text, size, bold=False, fill="#1A2238"):
    font = get_font(bold, size)
    draw_text_in_box(draw, box, text, font, fill=fill)


def make_qr(data_url, size):
    qr = qrcode.QRCode(border=1, box_size=10)
    qr.add_data(data_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((size, size), Image.LANCZOS)


def build_content_rows(has_aadhaar):
    total_budget = NAME_ROW_HEIGHT + 4 * LABEL_ROW_HEIGHT + 4 * ROW_GAP

    n_label_rows = 5 if has_aadhaar else 4
    gap = AADHAAR_ROW_GAP if has_aadhaar else ROW_GAP

    remaining = total_budget - NAME_ROW_HEIGHT
    label_h = (remaining - n_label_rows * gap) / n_label_rows

    name_row = (CONTENT_X0, NAME_ROW_TOP, CONTENT_X1, NAME_ROW_TOP + NAME_ROW_HEIGHT)

    rows = [name_row]
    cursor = NAME_ROW_TOP + NAME_ROW_HEIGHT

    for _ in range(n_label_rows):
        top = cursor + gap
        bottom = top + label_h
        rows.append((CONTENT_X0, top, CONTENT_X1, bottom))
        cursor = bottom

    return rows


def draw_land_table(draw, table_box, land_rows):
    x0, y0, x1, y1 = table_box
    total_w = x1 - x0
    total_h = y1 - y0

    headers = ["State", "District", "S. No.", "Owner Name", "Total Area", "Assigned Area"]
    weights = [0.16, 0.19, 0.10, 0.20, 0.17, 0.18]
    col_widths = [int(total_w * w) for w in weights]
    col_widths[-1] = total_w - sum(col_widths[:-1])

    n_rows = max(len(land_rows), 1)

    font_size = MAX_TABLE_FONT
    row_h = int(font_size * ROW_PADDING_RATIO)
    required_h = (n_rows + 1) * row_h

    if required_h > total_h:
        scale = total_h / required_h
        font_size = max(MIN_TABLE_FONT, int(font_size * scale))
        row_h = int(font_size * ROW_PADDING_RATIO)

    font_header = get_font(True, font_size)
    font_cell = get_font(False, font_size)

    cur_y = y0

    draw.rectangle([x0, cur_y, x1, cur_y + row_h], fill="#D9E6E3", outline="#1A2238", width=2)
    cx = x0
    for i, htext in enumerate(headers):
        cw = col_widths[i]
        draw.rectangle([cx, cur_y, cx + cw, cur_y + row_h], outline="#1A2238", width=1)
        draw_text_in_box(draw, (cx, cur_y, cx + cw, cur_y + row_h), htext, font_header)
        cx += cw
    cur_y += row_h

    if not land_rows:
        draw.rectangle([x0, cur_y, x1, cur_y + row_h], outline="#1A2238", width=1)
        draw_text_in_box(draw, (x0, cur_y, x1, cur_y + row_h), "No land record found", font_cell)
        return

    for row in land_rows:
        values = [row["state"], row["district"], row["s_no"], row["owner"], row["total_area"], row["assigned_area"]]
        cx = x0
        for i, val in enumerate(values):
            cw = col_widths[i]
            draw.rectangle([cx, cur_y, cx + cw, cur_y + row_h], outline="#1A2238", width=1)
            draw_text_in_box(draw, (cx, cur_y, cx + cw, cur_y + row_h), val, font_cell)
            cx += cw
        cur_y += row_h


# ============================================================
# CORE BUILDERS
# ============================================================
def build_front_card_image(pdf_bytes, farmer_id, aadhaar_number):
    data = extract_farmer_data(pdf_bytes)
    if data["photo"] is None:
        raise ValueError("No photo found in the PDF")

    has_aadhaar = bool(aadhaar_number)

    resp = requests.get(FRONT_CARD_TEMPLATE_URL, timeout=15)
    template = Image.open(io.BytesIO(resp.content)).convert("RGB")

    scale_x = template.width / TEMPLATE_W
    scale_y = template.height / TEMPLATE_H

    def scale_box(box):
        x0, y0, x1, y1 = box
        return (int(x0 * scale_x), int(y0 * scale_y), int(x1 * scale_x), int(y1 * scale_y))

    photo_box = scale_box(PHOTO_BOX)
    qr_box = scale_box(QR_BOX)
    farmer_id_box = scale_box(FARMER_ID_BOX)

    photo_box = shrink_box_asym(
        photo_box,
        left=int(PHOTO_PADDING_LEFT * scale_x),
        top=int(PHOTO_PADDING_TOP * scale_y),
        right=int(PHOTO_PADDING_RIGHT * scale_x),
        bottom=int(PHOTO_PADDING_BOTTOM * scale_y),
    )

    qr_padding_scaled = int(QR_PADDING * scale_x)
    qr_box = shrink_box_asym(qr_box, qr_padding_scaled, qr_padding_scaled, qr_padding_scaled, qr_padding_scaled)

    raw_rows = build_content_rows(has_aadhaar)
    scaled_rows = [scale_box(r) for r in raw_rows]

    if has_aadhaar:
        name_row, dob_row, gender_row, caste_row, aadhaar_row, mobile_row = scaled_rows
    else:
        name_row, dob_row, gender_row, caste_row, mobile_row = scaled_rows

    pw, ph = photo_box[2] - photo_box[0], photo_box[3] - photo_box[1]
    fitted_photo = cover_fit(data["photo"], pw, ph)
    template.paste(fitted_photo, (photo_box[0], photo_box[1]))

    draw = ImageDraw.Draw(template)

    name_font_size = int(NAME_FONT_SIZE * scale_y)
    draw_left_text(draw, name_row, data["name"], size=name_font_size, bold=False)

    label_size = int(LABEL_FONT_SIZE * scale_y)
    draw_label_value(draw, dob_row, "Date Of Birth  :", data["dob"], label_size=label_size)
    draw_label_value(draw, gender_row, "Gender  :", data["gender"], label_size=label_size)
    draw_label_value(draw, caste_row, "Caste  :", data["caste"], label_size=label_size)

    if has_aadhaar:
        draw_label_value(draw, aadhaar_row, "Aadhaar Number  :", aadhaar_number, label_size=label_size)

    draw_label_value(draw, mobile_row, "Phone Number  :", data["mobile"], label_size=label_size)

    id_font_size = int(FARMER_ID_FONT_SIZE * scale_y)
    draw_centered_text(draw, farmer_id_box, farmer_id, size=id_font_size, bold=True)

    qbw, qbh = qr_box[2] - qr_box[0], qr_box[3] - qr_box[1]
    qr_size = min(qbw, qbh)
    qr_img = make_qr(AGRISTACK_URL, qr_size)
    qx = qr_box[0] + (qbw - qr_size) // 2 + int(QR_SHIFT_X * scale_x)
    qy = qr_box[1] + (qbh - qr_size) // 2 + int(QR_SHIFT_Y * scale_y)
    template.paste(qr_img, (qx, qy))

    return template


def build_back_card_image(pdf_bytes):
    data = extract_back_data(pdf_bytes)

    resp = requests.get(BACK_CARD_TEMPLATE_URL, timeout=15)
    template = Image.open(io.BytesIO(resp.content)).convert("RGB")

    scale_x = template.width / BACK_TEMPLATE_W
    scale_y = template.height / BACK_TEMPLATE_H

    def scale_box(box):
        x0, y0, x1, y1 = box
        return (int(x0 * scale_x), int(y0 * scale_y), int(x1 * scale_x), int(y1 * scale_y))

    content_box = scale_box(BACK_CONTENT_BOX)
    cx0, cy0, cx1, cy1 = content_box

    draw = ImageDraw.Draw(template)

    address_x0 = cx0 + int(ADDRESS_LEFT_PADDING * scale_x)
    address_row = (address_x0, cy0, cx1, cy0 + int(ADDRESS_ROW_HEIGHT * scale_y))
    draw_label_value(
        draw, address_row, "Address  :", data["address"],
        label_size=int(ADDRESS_FONT_SIZE * scale_y)
    )

    table_top = cy0 + int((ADDRESS_ROW_HEIGHT + TABLE_TOP_GAP) * scale_y)
    table_box = (cx0, table_top, cx1, cy1)
    draw_land_table(draw, table_box, data["land_rows"])

    return template


# ============================================================
# PRINT-READY A4 SHEET — Ayushman page ke jaisa hi fixed math
# ============================================================
def build_print_pdf(front_img, back_img):
    canvas = Image.new("RGB", (A4_CANVAS_W, A4_CANVAS_H), "white")

    print_w = CARD_W * PRINT_SCALE
    print_h = CARD_H * PRINT_SCALE

    # Auto-center logic — bilkul Ayushman page jaisa
    total_content_width = (print_w * 2) + GAP_X
    left_col_x = (A4_CANVAS_W - total_content_width) / 2
    right_col_x = left_col_x + print_w + GAP_X

    current_y = START_Y

    front_resized = front_img.resize((int(print_w), int(print_h)), Image.LANCZOS)
    back_resized = back_img.resize((int(print_w), int(print_h)), Image.LANCZOS)

    canvas.paste(front_resized, (int(left_col_x), int(current_y)))
    canvas.paste(back_resized, (int(right_col_x), int(current_y)))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        [left_col_x, current_y, left_col_x + print_w, current_y + print_h],
        outline="#999999", width=2
    )
    draw.rectangle(
        [right_col_x, current_y, right_col_x + print_w, current_y + print_h],
        outline="#999999", width=2
    )

    return canvas


# ============================================================
# FRONT CARD ENDPOINT
# ============================================================
@app.route("/generate-card", methods=["POST"])
def generate_card():
    if "pdf" not in request.files:
        return jsonify({"error": "PDF file is required (field name: pdf)"}), 400

    farmer_id = request.form.get("farmer_id", "").strip()
    if not re.fullmatch(r"\d{11}", farmer_id):
        return jsonify({"error": "Farmer ID must be exactly 11 digits"}), 400

    aadhaar_number = request.form.get("aadhaar_number", "").strip()

    pdf_file = request.files["pdf"]
    pdf_bytes = pdf_file.read()

    try:
        template = build_front_card_image(pdf_bytes, farmer_id, aadhaar_number)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Could not generate the card: {str(e)}"}), 500

    output = io.BytesIO()
    template.save(output, format="PNG")
    output.seek(0)
    return send_file(output, mimetype="image/png", as_attachment=False, download_name="farmer-card-front.png")


# ============================================================
# BACK CARD ENDPOINT
# ============================================================
@app.route("/generate-card-back", methods=["POST"])
def generate_card_back():
    if "pdf" not in request.files:
        return jsonify({"error": "PDF file is required (field name: pdf)"}), 400

    pdf_file = request.files["pdf"]
    pdf_bytes = pdf_file.read()

    try:
        template = build_back_card_image(pdf_bytes)
    except Exception as e:
        return jsonify({"error": f"Could not generate the card: {str(e)}"}), 500

    output = io.BytesIO()
    template.save(output, format="PNG")
    output.seek(0)
    return send_file(output, mimetype="image/png", as_attachment=False, download_name="farmer-card-back.png")


# ============================================================
# PRINT-READY A4 PDF ENDPOINT
# ============================================================
@app.route("/generate-print-pdf", methods=["POST"])
def generate_print_pdf():
    if "pdf" not in request.files:
        return jsonify({"error": "PDF file is required (field name: pdf)"}), 400

    farmer_id = request.form.get("farmer_id", "").strip()
    if not re.fullmatch(r"\d{11}", farmer_id):
        return jsonify({"error": "Farmer ID must be exactly 11 digits"}), 400

    aadhaar_number = request.form.get("aadhaar_number", "").strip()

    pdf_file = request.files["pdf"]
    pdf_bytes = pdf_file.read()

    try:
        front_img = build_front_card_image(pdf_bytes, farmer_id, aadhaar_number)
        back_img = build_back_card_image(pdf_bytes)
        page = build_print_pdf(front_img, back_img)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Could not generate the print PDF: {str(e)}"}), 500

    output = io.BytesIO()
    page.save(output, format="PDF", resolution=300)
    output.seek(0)
    return send_file(
        output,
        mimetype="application/pdf",
        as_attachment=False,
        download_name="farmer-id-card-print.pdf"
    )


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "PVC Maker API is running"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
