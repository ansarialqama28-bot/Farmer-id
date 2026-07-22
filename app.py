import os
import re
import io
import requests
import qrcode
import pdfplumber
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)
CORS(app)

# ============================================================
# CONFIG
# ============================================================
FRONT_CARD_TEMPLATE_URL = "https://i.ibb.co/nFLxh2F/IMG-20260721-WA0005.jpg"
AGRISTACK_URL = "https://www.upfr.agristack.gov.in/farmer-registry-up/"

TEMPLATE_W, TEMPLATE_H = 1559, 1009

PHOTO_BOX      = (141, 274, 483, 709)
QR_BOX         = (1166, 459, 1347, 717)
FARMER_ID_BOX  = (518, 747, 1038, 897)

# ---- Text content zone (Name/DOB/Gender/Caste/Mobile isi patti mein) ----
CONTENT_X0 = 523
CONTENT_X1 = 1140
CONTENT_TOP = 255       # logo ke turant neeche se
CONTENT_BOTTOM = 745    # Farmer ID box shuru hone se just pehle tak

# ---- Photo: upar ki taraf zyada badhao, neeche thoda sa ----
PHOTO_PADDING_LEFT = 18
PHOTO_PADDING_RIGHT = 18
PHOTO_PADDING_TOP = 10     # bahut kam — photo upar tak badhega
PHOTO_PADDING_BOTTOM = 12  # thoda kam — neeche bhi thoda badhega

# ---- QR: aur bada + right shift ----
QR_PADDING = 2
QR_SHIFT_X = 26   # right ki taraf shift
QR_SHIFT_Y = 0

# ---- Text sizes — available row-height ke hisab se max practical size ----
NAME_FONT_SIZE = 95
LABEL_FONT_SIZE = 90
FARMER_ID_FONT_SIZE = 80

FONT_REGULAR_PATH = "fonts/Poppins-Regular.ttf"
FONT_BOLD_PATH = "fonts/Poppins-Bold.ttf"


def get_font(bold, size):
    path = FONT_BOLD_PATH if bold else FONT_REGULAR_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        # FIX: pehle yahan load_default() bina size ke tha, jo size ignore kar deta tha.
        # Ab size=size diya hai taaki fallback font bhi sahi size mein bade.
        try:
            return ImageFont.load_default(size=size)
        except Exception:
            return ImageFont.load_default()


# ============================================================
# PDF SE DATA NIKALNA
# ============================================================
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


def draw_centered_text(draw, box, text, size, bold=False, fill="#1A2238"):
    x0, y0, x1, y1 = box
    box_w, box_h = x1 - x0, y1 - y0
    font = get_font(bold, size)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = x0 + (box_w - tw) // 2
    ty = y0 + (box_h - th) // 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=fill)


def make_qr(data_url, size):
    qr = qrcode.QRCode(border=1, box_size=10)
    qr.add_data(data_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((size, size), Image.LANCZOS)


def build_content_rows():
    total_h = CONTENT_BOTTOM - CONTENT_TOP
    row_h = total_h // 5
    rows = []
    for i in range(5):
        y0 = CONTENT_TOP + i * row_h
        y1 = y0 + row_h
        rows.append((CONTENT_X0, y0, CONTENT_X1, y1))
    return rows


# ============================================================
# MAIN ENDPOINT
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
    draw_centered_text(draw, farmer_id_box, f"Farmer ID : {farmer_id}", size=id_font_size, bold=True)

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


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "PVC Maker API is running"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
