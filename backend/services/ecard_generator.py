import os
import sys
import logging
import tempfile
import requests
import jwt
from datetime import datetime, timezone, timedelta
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import qrcode

# ReportLab imports for PDF compilation
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Image as RLImage, Spacer, Table, TableStyle
from reportlab.lib import colors

# App database & storage config
from config.settings import Config
from services.storage_service import upload_file as upload_to_storage

logger = logging.getLogger(__name__)

# Constants for e-card size (horizontal aspect ratio ~ 1.585)
CARD_WIDTH = 1010
CARD_HEIGHT = 638

def _load_font(font_type="regular", size=14):
    """Load Arial or fall back to Default font depending on OS environment."""
    try:
        if sys.platform == "win32":
            font_path = "C:\\Windows\\Fonts\\arial.ttf" if font_type == "regular" else "C:\\Windows\\Fonts\\arialbd.ttf"
        else:
            # Common linux paths
            for path in [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/TTF/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
            ]:
                if os.path.exists(path):
                    font_path = path
                    break
            else:
                font_path = None
        
        if font_path and os.path.exists(font_path):
            return ImageFont.truetype(font_path, size)
    except Exception as e:
        logger.warning(f"[ECard] Failed to load TrueType font: {e}")
    return ImageFont.load_default()

def _download_profile_photo(url: str) -> str | None:
    """Download profile photo to a temp file."""
    if not url or not url.startswith("http"):
        return None
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        suffix = Path(url.split("?")[0]).suffix or ".png"
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_file.write(res.content)
        temp_file.close()
        return temp_file.name
    except Exception as e:
        logger.warning(f"[ECard] Failed to download profile photo from {url}: {e}")
        return None

def generate_signed_verification_token(ppo_number: str, mobile: str) -> str:
    """Generate a secure, digitally signed token with a 10-year expiration."""
    payload = {
        "sub": str(ppo_number),
        "mobile": str(mobile),
        "purpose": "ecard_verification",
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int((datetime.now(timezone.utc) + timedelta(days=365 * 10)).timestamp())
    }
    return jwt.encode(payload, Config.JWT_SECRET_KEY or Config.SECRET_KEY, algorithm="HS256")

def decode_verification_token(token: str) -> tuple[dict | None, str | None]:
    """Decode and cryptographically verify the token."""
    try:
        payload = jwt.decode(token, Config.JWT_SECRET_KEY or Config.SECRET_KEY, algorithms=["HS256"])
        if payload.get("purpose") != "ecard_verification":
            return None, "Invalid verification token purpose."
        return payload, None
    except jwt.ExpiredSignatureError:
        return None, "Verification token has expired."
    except jwt.InvalidTokenError:
        return None, "Verification signature is invalid."

def generate_ecard_assets(profile_data: dict) -> dict | None:
    """
    Renders Front and Back card PNGs, compiles them into a printable A4 PDF,
    uploads them to Supabase storage, and returns their URLs.
    """
    temp_files = []
    try:
        template_path = Path(__file__).resolve().parent.parent / "resources" / "e-card" / "ecard.jpeg"
        if not template_path.exists():
            logger.error(f"[ECard] Background template ecard.jpeg not found at: {template_path}")
            return None
        
        # Load and scale background template
        bg_image = Image.open(template_path).convert("RGBA")
        bg_image = bg_image.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)

        # ----------------------------------------------------
        # 1. RENDER FRONT CARD
        # ----------------------------------------------------
        front = bg_image.copy()
        draw_f = ImageDraw.Draw(front)

        # Fonts (all sizes scaled to at least 14px)
        font_title = _load_font("bold", 24)
        font_subtitle = _load_font("bold", 16)
        font_label = _load_font("bold", 14)
        font_val = _load_font("regular", 14)
        font_val_bold = _load_font("bold", 14)
        font_status = _load_font("bold", 14)

        # Draw Branding / Header (All text is black)
        draw_f.text((40, 30), "GOVERNMENT OF TAMIL NADU", fill=(0, 0, 0, 255), font=font_title)
        draw_f.text((40, 65), "PENSIONERS HEALTH SCHEME — e-HEALTH CARD", fill=(0, 0, 0, 255), font=font_subtitle)
        
        # Header separator line
        draw_f.line([(40, 95), (CARD_WIDTH - 40, 95)], fill=(0, 0, 0, 255), width=2)

        # Draw Photo
        raw_photo = (
            profile_data.get("profilePhoto") or 
            profile_data.get("profile", {}).get("profilePhoto") or 
            profile_data.get("profile", {}).get("photo") or 
            profile_data.get("profile", {}).get("photo_url") or
            profile_data.get("documents", {}).get("profilePhoto")
        )
        if isinstance(raw_photo, dict):
            photo_url = raw_photo.get("url")
        else:
            photo_url = raw_photo
        photo_path = _download_profile_photo(photo_url)
        
        photo_x, photo_y = 45, 125
        photo_w, photo_h = 170, 205
        
        if photo_path and os.path.exists(photo_path):
            try:
                photo_img = Image.open(photo_path).convert("RGBA")
                photo_img = photo_img.resize((photo_w, photo_h), Image.Resampling.LANCZOS)
                front.paste(photo_img, (photo_x, photo_y), photo_img)
                # Outer photo border
                draw_f.rectangle([(photo_x, photo_y), (photo_x + photo_w, photo_y + photo_h)], outline=(0, 0, 0, 255), width=2)
                temp_files.append(photo_path)
            except Exception as e:
                logger.error(f"[ECard] Failed to paste profile photo: {e}")
                draw_f.rectangle([(photo_x, photo_y), (photo_x + photo_w, photo_y + photo_h)], fill=(226, 232, 240, 255), outline=(0, 0, 0, 255), width=2)
                draw_f.text((photo_x + 35, photo_y + 90), "[ Photo ]", fill=(0, 0, 0, 255), font=font_label)
        else:
            # Draw placeholder photo box
            draw_f.rectangle([(photo_x, photo_y), (photo_x + photo_w, photo_y + photo_h)], fill=(226, 232, 240, 255), outline=(0, 0, 0, 255), width=2)
            draw_f.text((photo_x + 35, photo_y + 90), "[ Photo ]", fill=(0, 0, 0, 255), font=font_label)

        # Pensioner details mapping (starts at X=245)
        ppo_num = profile_data.get("ppo_number") or profile_data.get("ppoNumber") or "N/A"
        raw_aadhaar = profile_data.get("aadhaar_number") or profile_data.get("aadhaar") or ""
        # Mask Aadhaar (e.g. XXXX XXXX 1234)
        masked_aadhaar = "XXXX XXXX " + raw_aadhaar[-4:] if len(raw_aadhaar) >= 4 else "XXXX XXXX XXXX"
        unique_id = f"MC-{ppo_num}"
        dob = profile_data.get("dob") or profile_data.get("date_of_birth") or "N/A"
        blood_group = profile_data.get("blood_group") or "N/A"
        issue_date = datetime.now().strftime("%d-%m-%Y")
        pensioner_name = profile_data.get("name") or profile_data.get("beneficiary_name") or profile_data.get("full_name") or "N/A"

        details = [
            ("NAME OF BENEFICIARY", pensioner_name, True),
            ("UNIQUE ID", unique_id, True),
            ("PPO NUMBER", ppo_num, False),
            ("AADHAAR NUMBER", masked_aadhaar, False),
            ("DATE OF BIRTH", dob, False),
            ("BLOOD GROUP", blood_group, False),
            ("DATE OF ISSUE", issue_date, False),
        ]

        curr_y = 125
        for label, val, is_bold in details:
            draw_f.text((245, curr_y), label, fill=(0, 0, 0, 255), font=font_label)
            f_val = font_val_bold if is_bold else font_val
            draw_f.text((430, curr_y), f":  {val}", fill=(0, 0, 0, 255), font=f_val)
            curr_y += 30

        # Draw Hologram Watermark (UI Only - low opacity verification shield)
        overlay = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (255, 255, 255, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        # Draw concentric validation rings in black with low opacity
        overlay_draw.arc([CARD_WIDTH - 250, CARD_HEIGHT - 220, CARD_WIDTH - 70, CARD_HEIGHT - 40], start=0, end=360, fill=(0, 0, 0, 24), width=3)
        overlay_draw.arc([CARD_WIDTH - 230, CARD_HEIGHT - 200, CARD_WIDTH - 90, CARD_HEIGHT - 60], start=0, end=360, fill=(0, 0, 0, 16), width=1)
        font_hologram = _load_font("bold", 14)
        overlay_draw.text((CARD_WIDTH - 205, CARD_HEIGHT - 138), "SECURE DIGITAL\n VERIFICATION", fill=(0, 0, 0, 40), font=font_hologram)
        front = Image.alpha_composite(front, overlay)
        draw_f = ImageDraw.Draw(front) # rebind

        # Draw Card Valid status badge with black text and border
        card_status = profile_data.get("ecard_status") or "ACTIVE"
        draw_f.rounded_rectangle([(40, CARD_HEIGHT - 70), (280, CARD_HEIGHT - 35)], radius=6, outline=(0, 0, 0, 255), width=2)
        draw_f.text((65, CARD_HEIGHT - 62), f"VALID STATUS: {card_status.upper()}", fill=(0, 0, 0, 255), font=font_status)

        # Save Front Card image
        temp_dir = tempfile.gettempdir()
        front_png_path = os.path.join(temp_dir, f"ecard_front_{ppo_num}.png")
        front_rgb = front.convert("RGB")
        front_rgb.save(front_png_path, "PNG")
        temp_files.append(front_png_path)

        # ----------------------------------------------------
        # 2. RENDER BACK CARD
        # ----------------------------------------------------
        back = bg_image.copy()
        draw_b = ImageDraw.Draw(back)

        # Draw Back Header
        draw_b.text((40, 30), "BENEFICIARY e-CARD DETAILS", fill=(0, 0, 0, 255), font=font_title)
        draw_b.line([(40, 65), (CARD_WIDTH - 40, 65)], fill=(0, 0, 0, 255), width=2)

        # Draw details starting Y=95
        mobile = profile_data.get("mobile_number") or profile_data.get("mobile") or "N/A"
        emergency_phone = profile_data.get("emergency_phone") or profile_data.get("emergency", {}).get("phone") or "N/A"
        emergency_name = profile_data.get("emergency_contact") or profile_data.get("emergency", {}).get("name") or "N/A"
        treasury = profile_data.get("treasury_office") or "N/A"
        
        # Get address string
        addr = profile_data.get("address") or {}
        if isinstance(addr, dict):
            addr_str = f"{addr.get('doorNo', '')} {addr.get('street', '')}, {addr.get('area', '')}, {addr.get('district', '')} - {addr.get('pincode', '')}".strip(", ")
        else:
            addr_str = str(addr)
        if not addr_str:
            addr_str = "N/A"

        back_details = [
            ("RESIDENTIAL ADDRESS", addr_str[:120]),
            ("MOBILE NUMBER", mobile),
            ("EMERGENCY CONTACT", f"{emergency_name} ({emergency_phone})"),
            ("TREASURY OFFICE", treasury)
        ]

        curr_y = 95
        for label, val in back_details:
            draw_b.text((40, curr_y), label, fill=(0, 0, 0, 255), font=font_label)
            # Support wrapping for long address
            if len(val) > 55 and label == "RESIDENTIAL ADDRESS":
                draw_b.text((40, curr_y + 20), val[:55], fill=(0, 0, 0, 255), font=font_val)
                draw_b.text((40, curr_y + 40), val[55:110], fill=(0, 0, 0, 255), font=font_val)
                curr_y += 65
            else:
                draw_b.text((250, curr_y), f":  {val}", fill=(0, 0, 0, 255), font=font_val)
                curr_y += 35

        # Generate Secure QR Code
        qr_token = generate_signed_verification_token(ppo_num, mobile)
        domain = getattr(Config, "BASE_URL", None) or "http://localhost:5000"
        qr_url = f"{domain}/officer/verify_card/{qr_token}"

        qr = qrcode.QRCode(version=1, box_size=8, border=1)
        qr.add_data(qr_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
        
        # Paste QR code inside a curved border box (bottom right)
        box_x1, box_y1 = 770, 360
        box_x2, box_y2 = 960, 550
        box_radius = 15
        
        # Draw curved border box around the QR code
        draw_b.rounded_rectangle([(box_x1, box_y1), (box_x2, box_y2)], radius=box_radius, outline=(0, 0, 0, 255), width=2)
        
        # Resize QR code to 150x150 (reduced) and center it inside the 190x190 box
        qr_size = 150
        qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)
        qr_x = box_x1 + (box_x2 - box_x1 - qr_size) // 2
        qr_y = box_y1 + (box_y2 - box_y1 - qr_size) // 2
        back.paste(qr_img, (qr_x, qr_y), qr_img)

        # Draw Verification details under QR code (size at least 14px)
        font_desc = _load_font("bold", 14)
        draw_b.text((box_x1 + 10, box_y2 + 15), f"Digital ID: {ppo_num}", fill=(0, 0, 0, 255), font=font_desc)

        # Draw Disclaimer at bottom left (size at least 14px)
        disclaimer_text = (
            "DISCLAIMER: This card is valid only for MediCurance digital verification\n"
            "and reimbursement claims under the Tamil Nadu Pensioners Health Scheme.\n"
            "It does not serve as a legal identifier for any other department or service."
        )
        font_disc = _load_font("regular", 14)
        draw_b.text((40, CARD_HEIGHT - 90), disclaimer_text, fill=(0, 0, 0, 255), font=font_disc)

        # Save Back Card image
        back_png_path = os.path.join(temp_dir, f"ecard_back_{ppo_num}.png")
        back_rgb = back.convert("RGB")
        back_rgb.save(back_png_path, "PNG")
        temp_files.append(back_png_path)

        # ----------------------------------------------------
        # 3. COMPILE WALLET PDF (A4 Page with Front & Back stacked)
        # ----------------------------------------------------
        pdf_path = os.path.join(temp_dir, f"ecard_wallet_{ppo_num}.pdf")
        
        # Setup page margins
        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=A4,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36
        )

        # Resize images for PDF display (~3.5 inches x ~2.2 inches standard, scaling to 360 x 228 pt)
        pdf_w, pdf_h = 360, 228
        rl_front = RLImage(front_png_path, width=pdf_w, height=pdf_h)
        rl_back = RLImage(back_png_path, width=pdf_w, height=pdf_h)

        # Build centered two-page layout using PageBreak
        from reportlab.platypus import PageBreak
        story = [
            Spacer(1, 200),  # Center Front card vertically on Page 1
            rl_front,
            PageBreak(),     # Push to Page 2
            Spacer(1, 200),  # Center Back card vertically on Page 2
            rl_back
        ]
        
        doc.build(story)
        temp_files.append(pdf_path)

        # ----------------------------------------------------
        # 4. UPLOAD ASSETS TO SUPABASE STORAGE
        # ----------------------------------------------------
        front_url = upload_to_storage(front_png_path, folder="ecards", bucket_name=Config.SUPABASE_LETTER_BUCKET)
        back_url = upload_to_storage(back_png_path, folder="ecards", bucket_name=Config.SUPABASE_LETTER_BUCKET)
        pdf_url = upload_to_storage(pdf_path, folder="ecards", bucket_name=Config.SUPABASE_LETTER_BUCKET)

        if not front_url or not back_url or not pdf_url:
            logger.error("[ECard] Failed to upload assets to Supabase storage.")
            return None

        # Return results
        return {
            "front_url": front_url,
            "back_url": back_url,
            "pdf_url": pdf_url,
            "verification_token": qr_token,
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "status": card_status.upper()
        }

    except Exception as ex:
        logger.error(f"[ECard] Failed to generate ecard assets: {ex}", exc_info=True)
        return None
    finally:
        # Clean up temporary local files
        for path in temp_files:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

def update_user_ecard_metadata(mobile: str, ecard_metadata: dict) -> bool:
    """Updates pensioner e-card metadata in govtlist or users collections."""
    try:
        from database.mongo_client import govt_collection, users_collection
        phone_digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
        query = {"$or": [{"auth.phone": phone_digits}, {"mobile": phone_digits}, {"phone": phone_digits}]}
        
        res = govt_collection.update_one(query, {"$set": {"ecard": ecard_metadata}})
        if res.matched_count == 0:
            users_collection.update_one(query, {"$set": {"ecard": ecard_metadata}})
            
        logger.info(f"[ECard] E-card metadata successfully saved for mobile: {mobile}")
        return True
    except Exception as e:
        logger.error(f"[ECard] Failed to update e-card metadata: {e}", exc_info=True)
        return False

def generate_and_save_ecard(mobile: str, employee_or_user: dict) -> dict | None:
    """Orchestrator to resolve pensioner details, draw card files, compile PDF, and save URLs."""
    from services.pensioner_service import build_pensioner_profile
    doc = employee_or_user or {}
    p_data = build_pensioner_profile(doc)
    
    addr_dict = p_data.get("address") or {}
    raw_aadhaar = doc.get("aadhaar_number") or p_data.get("identity", {}).get("aadhaarLast4") or ""
    
    flat = {
        "name": p_data.get("profile", {}).get("fullName") or doc.get("name") or doc.get("full_name") or "N/A",
        "ppo_number": p_data.get("auth", {}).get("ppoNumber") or doc.get("ppo_number") or "N/A",
        "aadhaar_number": raw_aadhaar,
        "mobile_number": p_data.get("auth", {}).get("phone") or doc.get("mobile") or doc.get("phone") or "N/A",
        "emergency_contact": p_data.get("emergency", {}).get("name") or doc.get("emergency_contact") or "N/A",
        "emergency_phone": p_data.get("emergency", {}).get("phone") or doc.get("emergency_phone") or "N/A",
        "dob": p_data.get("profile", {}).get("dob") or doc.get("date_of_birth") or "N/A",
        "blood_group": p_data.get("medical", {}).get("bloodGroup") or doc.get("blood_group") or "N/A",
        "treasury_office": doc.get("treasury_office") or p_data.get("pension", {}).get("treasuryOffice") or "N/A",
        "address": addr_dict,
        "profilePhoto": (
            doc.get("profilePhoto") or
            doc.get("profile", {}).get("profilePhoto") or
            p_data.get("profile", {}).get("photo") or
            p_data.get("profile", {}).get("photo_url") or
            (doc.get("documents", {}).get("profilePhoto", {}).get("url") if isinstance(doc.get("documents", {}).get("profilePhoto"), dict) else doc.get("documents", {}).get("profilePhoto")) or
            (p_data.get("documents", {}).get("profilePhoto", {}).get("url") if isinstance(p_data.get("documents", {}).get("profilePhoto"), dict) else p_data.get("documents", {}).get("profilePhoto")) or ""
        ),
        "ecard_status": doc.get("ecard", {}).get("status") or "ACTIVE"
    }
    
    assets = generate_ecard_assets(flat)
    if assets:
        update_user_ecard_metadata(mobile, assets)
    return assets
