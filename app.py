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
# CONFIG — FRONT CARD (aapka pichla code, bilkul same rakha hai)
# ============================================================
FRONT_CARD_TEMPLATE_URL = "https://i.ibb.co/nFLxh2F/IMG-20260721-WA0005.jpg"
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
# CONFIG — BACK CARD (naya)
# ============================================================
BACK_CARD_TEMPLATE_URL = "back template url here"

# Reference resolution jis par neeche ke coordinates measure kiye gaye hain
BACK_TEMPLATE_W, BACK_TEMPLATE_H = 1537, 1023

# Golden border ke andar wala poora usable content area
BACK_CONTENT_BOX = (130, 110, 1407, 913)   # x0, y0, x1, y1

ADDRESS_ROW_HEIGHT = 90
ADDRESS_FONT_SIZE = 42

TABLE_TOP_GAP = 30          # address ke neeche table shuru hone se pehle ka gap
MAX_TABLE_ROW_HEIGHT = 85   # ek row ki max height (kam rows honge to isse zyada bada nahi hogi)
MIN_TABLE_FONT = 20
MAX_TABLE_FONT = 38

FONT_HINDI_PATH = "NotoSansDevanagari-Regular.ttf"


def get_font(bold, size):
    path = FONT_BOLD_PATH if bold else FONT_REGULAR_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except Exception:
            return ImageFont.load_default()


def get_hindi_font(size):
    try:
        return ImageFont.truetype(FONT_HINDI_PATH, size)
    except Exception:
        # Hindi font na mile to English font hi use ho jayega (Hindi text tab tofu/blank dikh sakta hai)
        return get_font(False, size)


# ============================================================
# PDF SE FRONT DATA NIKALNA (bilkul aapke pichle code jaisa)
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
# PDF SE BACK DATA NIKALNA (naya) — Address + Land Ownership Table
# ============================================================
def clean_cell(value):
    """PDF table cell ke andar ke line-breaks ko space se replace karta hai aur trailing comma hata deta hai."""
    if value is None:
        return ""
    v = value.replace("\n", " ")
    v = re.sub(r"\s+", " ", v).strip()
    v = v.rstrip(",").strip()
    return v


def extract_back_data(pdf_bytes):
    address = "N/A"
    land_rows = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        # ---- Address: page 1 ke text se ----
        page0_text = pdf.pages[0].extract_text() or ""
        m = re.search(r"Address In English\s+(.+?)\s+Address In Local Language", page0_text)
        if m:
            address = m.group(1).strip()

        # ---- Land Ownership Table: doosre page par milti hai ----
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table or len(table) < 2:
                    continue
                header = [clean_cell(c).lower() for c in table[0] if c is not None]
                header_joined = " ".join(header)
                if "owner" not in header_joined or "extent" not in header_joined:
                    continue  # ye land ownership table nahi hai

                for row in table[1:]:
                    if not row or len(row) < 12:
                        continue
                    state = clean_cell(row[0])
                    district = clean_cell(row[1])
                    s_no_raw = clean_cell(row[4])
                    s_no_match = re.match(r"(\d+)", s_no_raw)
                    s_no = s_no_match.group(1) if s_no_match else s_no_raw
                    owner = clean_cell(row[6])
                    total_area = clean_cell(row[10])
                    assigned_area = clean_cell(row[11])

                    if state and owner:
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
# IMAGE HELPERS (front)
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
    """Diya gaya font object seedha use karke box ke center mein text likhta hai."""
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


def build_content_rows():
    name_row = (CONTENT_X0, NAME_ROW_TOP, CONTENT_X1, NAME_ROW_TOP + NAME_ROW_HEIGHT)

    dob_top = NAME_ROW_TOP + NAME_ROW_HEIGHT + ROW_GAP
    dob_row = (CONTENT_X0, dob_top, CONTENT_X1, dob_top + LABEL_ROW_HEIGHT)

    gender_top = dob_top + LABEL_ROW_HEIGHT + ROW_GAP
    gender_row = (CONTENT_X0, gender_top, CONTENT_X1, gender_top + LABEL_ROW_HEIGHT)

    caste_top = gender_top + LABEL_ROW_HEIGHT + ROW_GAP
    caste_row = (CONTENT_X0, caste_top, CONTENT_X1, caste_top + LABEL_ROW_HEIGHT)

    mobile_top = caste_top + LABEL_ROW_HEIGHT + ROW_GAP
    mobile_row = (CONTENT_X0, mobile_top, CONTENT_X1, mobile_top + LABEL_ROW_HEIGHT)

    return name_row, dob_row, gender_row, caste_row, mobile_row


# ============================================================
# TABLE DRAWING (back card ke liye naya)
# ============================================================
def draw_land_table(draw, table_box, land_rows):
    x0, y0, x1, y1 = table_box
    total_w = x1 - x0
    total_h = y1 - y0

    headers = ["State", "District", "S. No.", "Owner Name", "Total Area", "Assigned Area"]
    hindi_cols = {3}  # sirf "Owner Name" column Hindi font se likhega
    weights = [0.16, 0.19, 0.10, 0.20, 0.17, 0.18]
    col_widths = [int(total_w * w) for w in weights]
    col_widths[-1] = total_w - sum(col_widths[:-1])  # rounding fix

    n_rows = max(len(land_rows), 1)
    row_h = min(MAX_TABLE_ROW_HEIGHT, total_h / (n_rows + 1))

    font_size = int(row_h * 0.4)
    font_size = max(MIN_TABLE_FONT, min(MAX_TABLE_FONT, font_size))

    font_header = get_font(True, font_size)
    font_cell = get_font(False, font_size)
    font_hindi = get_hindi_font(font_size)

    cur_y = y0

    # ---- Header row ----
    draw.rectangle([x0, cur_y, x1, cur_y + row_h], fill="#D9E6E3", outline="#1A2238", width=2)
    cx = x0
    for i, htext in enumerate(headers):
        cw = col_widths[i]
        draw.rectangle([cx, cur_y, cx + cw, cur_y + row_h], outline="#1A2238", width=1)
        draw_text_in_box(draw, (cx, cur_y, cx + cw, cur_y + row_h), htext, font_header)
        cx += cw
    cur_y += row_h

    # ---- Data rows (jitni bhi ho sakti hain, dynamically) ----
    if not land_rows:
        draw.rectangle([x0, cur_y, x1, cur_y + row_h], outline="#1A2238", width=1)
        draw_text_in_box(draw, (x0, cur_y, x1, cur_y + row_h), "Koi land record nahi mila", font_cell)
        return

    for row in land_rows:
        values = [row["state"], row["district"], row["s_no"], row["owner"], row["total_area"], row["assigned_area"]]
        cx = x0
        for i, val in enumerate(values):
            cw = col_widths[i]
            draw.rectangle([cx, cur_y, cx + cw, cur_y + row_h], outline="#1A2238", width=1)
            f = font_hindi if i in hindi_cols else font_cell
            draw_text_in_box(draw, (cx, cur_y, cx + cw, cur_y + row_h), val, f)
            cx += cw
        cur_y += row_h


# ============================================================
# FRONT CARD ENDPOINT (aapka pichla code, bilkul waisa hi)
# ============================================================
@app.route("/generate-card", methods=["POST"])
def generate_card():
    if "pdf" not in request.files:
        return jsonify({"error": "PDF file zaroori hai (field name: pdf)"}), 400

    farmer_id = request.form.get("farmer_id", "").strip()
    if not re.fullmatch(r"\d{11}", farmer_id):
        return jsonify({"error": "Farmer ID exactly 11 digits ki honi chahiye"}), 400

    pdf_file = request.files["pdf"]
    pdf_bytes = pdf_file.read()

    try:
        data = extract_farmer_data(pdf_bytes)
    except Exception as e:
        return jsonify({"error": f"PDF read nahi ho payi: {str(e)}"}), 500

    if data["photo"] is None:
        return jsonify({"error": "PDF mein photo nahi mili"}), 400

    try:
        resp = requests.get(FRONT_CARD_TEMPLATE_URL, timeout=15)
        template = Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        return jsonify({"error": f"Template load nahi hua: {str(e)}"}), 500

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

    name_row, dob_row, gender_row, caste_row, mobile_row = [scale_box(r) for r in build_content_rows()]

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
    draw_label_value(draw, mobile_row, "Phone Number  :", data["mobile"], label_size=label_size)

    id_font_size = int(FARMER_ID_FONT_SIZE * scale_y)
    draw_centered_text(draw, farmer_id_box, farmer_id, size=id_font_size, bold=True)

    qbw, qbh = qr_box[2] - qr_box[0], qr_box[3] - qr_box[1]
    qr_size = min(qbw, qbh)
    qr_img = make_qr(AGRISTACK_URL, qr_size)
    qx = qr_box[0] + (qbw - qr_size) // 2 + int(QR_SHIFT_X * scale_x)
    qy = qr_box[1] + (qbh - qr_size) // 2 + int(QR_SHIFT_Y * scale_y)
    template.paste(qr_img, (qx, qy))

    output = io.BytesIO()
    template.save(output, format="PNG")
    output.seek(0)
    return send_file(output, mimetype="image/png", as_attachment=False, download_name="farmer-card-front.png")


# ============================================================
# BACK CARD ENDPOINT (naya)
# ============================================================
@app.route("/generate-card-back", methods=["POST"])
def generate_card_back():
    if "pdf" not in request.files:
        return jsonify({"error": "PDF file zaroori hai (field name: pdf)"}), 400

    pdf_file = request.files["pdf"]
    pdf_bytes = pdf_file.read()

    try:
        data = extract_back_data(pdf_bytes)
    except Exception as e:
        return jsonify({"error": f"PDF read nahi ho payi: {str(e)}"}), 500

    try:
        resp = requests.get(BACK_CARD_TEMPLATE_URL, timeout=15)
        template = Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        return jsonify({"error": f"Template load nahi hua: {str(e)}"}), 500

    scale_x = template.width / BACK_TEMPLATE_W
    scale_y = template.height / BACK_TEMPLATE_H

    def scale_box(box):
        x0, y0, x1, y1 = box
        return (int(x0 * scale_x), int(y0 * scale_y), int(x1 * scale_x), int(y1 * scale_y))

    content_box = scale_box(BACK_CONTENT_BOX)
    cx0, cy0, cx1, cy1 = content_box

    draw = ImageDraw.Draw(template)

    # ---- Address heading ----
    address_row = (cx0, cy0, cx1, cy0 + int(ADDRESS_ROW_HEIGHT * scale_y))
    draw_label_value(
        draw, address_row, "Address  :", data["address"],
        label_size=int(ADDRESS_FONT_SIZE * scale_y)
    )

    # ---- Land Ownership Table (dynamic rows) ----
    table_top = cy0 + int((ADDRESS_ROW_HEIGHT + TABLE_TOP_GAP) * scale_y)
    table_box = (cx0, table_top, cx1, cy1)
    draw_land_table(draw, table_box, data["land_rows"])

    output = io.BytesIO()
    template.save(output, format="PNG")
    output.seek(0)
    return send_file(output, mimetype="image/png", as_attachment=False, download_name="farmer-card-back.png")


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "PVC Maker API is running"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
