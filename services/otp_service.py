import secrets
import string
import time
from config.settings import Config
from database.mongo_client import otp_collection


class OtpService:
    def __init__(self, otp_collection):
        self.otp_collection = otp_collection

    def _is_dev_mode(self):
        return str(Config.FLASK_ENV).lower() == "development"

    def generate_otp(self):
        """Generate a cryptographically secure 6-digit numeric OTP."""
        digits = string.digits
        return ''.join(secrets.choice(digits) for _ in range(6))

    def send_sms_otp(self, mobile, otp):
        # Future Twilio integration point:
        # replace this MSG91 transport with Twilio Verify/SMS once production OTP delivery is enabled.
        if not self._is_dev_mode():
            return {"status": "skipped", "message": "OTP delivery disabled outside development"}

        if not Config.MSG91_API_KEY:
            return {"status": "skipped", "message": "No API key configured"}

        url = Config.MSG91_API_URL

        payload = {
            "template_id": Config.MSG91_TEMPLATE_ID,
            "sender": Config.MSG91_SENDER_ID,
            "short_url": "0",
            "mobiles": f"91{mobile}",
            "OTP": otp
        }

        headers = {
            "authkey": Config.MSG91_API_KEY,
            "Content-Type": "application/json"
        }

        try:
            import requests
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            return response.json()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def store_otp(self, mobile):
        """Generate, store, and trigger sending of OTP in development only."""
        otp = self.generate_otp()

        if self._is_dev_mode():
            # Keep OTP persistence lightweight and dev-only so production data stays clean.
            self.otp_collection.update_one(
                {"mobile": mobile},
                {
                    "$set": {
                        "mobile": mobile,
                        "otp": otp,
                        "timestamp": time.time(),
                        "attempts": 0
                    }
                },
                upsert=True
            )

        # Trigger SMS only in dev; production SMS wiring can be switched to Twilio later.
        self.send_sms_otp(mobile, otp)

        return otp

    def verify_otp(self, mobile, user_otp):
        """
        Verify OTP with brute-force protection and expiration check.
        Returns (is_valid, error_message)
        """
        if not self._is_dev_mode():
            return False, "OTP verification is disabled outside development mode."

        record = self.otp_collection.find_one({"mobile": mobile})

        if not record:
            return False, "OTP not found or expired. Please request a new one."

        if record.get("attempts", 0) >= 5:
            self.otp_collection.delete_one({"mobile": mobile})
            return False, "Too many failed attempts. This OTP is now invalid."

        if time.time() - record["timestamp"] > Config.OTP_EXPIRY:
            self.otp_collection.delete_one({"mobile": mobile})
            return False, "OTP has expired. Please request a new one."

        if record["otp"] != user_otp:
            self.otp_collection.update_one(
                {"mobile": mobile},
                {"$inc": {"attempts": 1}}
            )
            return False, f"Invalid OTP. {5 - (record.get('attempts', 0) + 1)} attempts remaining."

        self.otp_collection.delete_one({"mobile": mobile})
        return True, "Verified"

# Export singleton functions to match blueprint usage
_otp_service = OtpService(otp_collection)
store_otp = _otp_service.store_otp
verify_otp = _otp_service.verify_otp
