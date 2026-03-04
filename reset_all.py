import os
import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
import cloudinary
import cloudinary.api

load_dotenv()

# Cloudinary Setup
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)

card_folder = os.getenv("CLOUDINARY_CARDS_FOLDER", "generated_cards")
photo_folder = os.getenv("CLOUDINARY_PHOTO_FOLDER", "member_photos")

try:
    print("Deleting Cloudinary resources...")
    res1 = cloudinary.api.delete_resources_by_prefix(card_folder)
    print(f"Deleted cards: {res1}")
    res2 = cloudinary.api.delete_resources_by_prefix(photo_folder)
    print(f"Deleted photos: {res2}")
except Exception as e:
    print(f"Cloudinary delete error: {e}")

# MongoDB Setup
uri = os.getenv('MONGO_URI')
client = MongoClient(uri, tlsCAFile=certifi.where())
db = client[os.getenv('MONGO_DB_NAME')]

# Collections to clear
stats_col = db[os.getenv('MONGO_STATS_COLLECTION', 'generation_stats')]
otp_col = db["otps"]

res_stats = stats_col.delete_many({})
print(f"Deleted {res_stats.deleted_count} generation stats.")

res_otp = otp_col.delete_many({})
print(f"Deleted {res_otp.deleted_count} OTP records.")
