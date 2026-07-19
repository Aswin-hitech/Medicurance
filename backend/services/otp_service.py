import secrets
import string
import time
from config.settings import Config
from database.mongo_client import otp_collection


class OtpService:
    def __init__(self, otp_collection):
        self.otp_collection = otp_collection

    def generate_otp(self):
        """Generate a cryptographically secure 6-digit numeric OTP."""
        digits = string.digits
        return ''.join(secrets.choice(digits) for _ in range(6))

    def send_sms_otp(self, mobile, otp):
        """Send OTP via Twilio REST API."""
        if not Config.TWILIO_ACCOUNT_SID or not Config.TWILIO_AUTH_TOKEN or not Config.TWILIO_PHONE_NUMBER:
            return {"status": "skipped", "message": "Twilio not configured"}

        url = f"https://api.twilio.com/2010-04-01/Accounts/{Config.TWILIO_ACCOUNT_SID}/Messages.json"
        
        payload = {
            "To": f"+91{mobile}",
            "From": Config.TWILIO_PHONE_NUMBER,
            "Body": f"Your Medicurance OTP is {otp}"
        }

        try:
            import requests
            response = requests.post(
                url, 
                data=payload, 
                auth=(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN),
                timeout=10
            )
            return response.json()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def store_otp(self, mobile):
        """Generate, store, and trigger sending of OTP."""
        otp = self.generate_otp()
        
        print(f"\n[OTP Service] Generated OTP for {mobile}: {otp}\n")

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

        self.send_sms_otp(mobile, otp)

        return otp

    def verify_otp(self, mobile, user_otp):
        """
        Verify OTP with brute-force protection and expiration check.
        Returns (is_valid, error_message)
        """
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
