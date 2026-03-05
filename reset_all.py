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

# MongoDB 1 — voter data only (no generation data here anymore)
uri = os.getenv('MONGO_URI')
client = MongoClient(uri, tlsCAFile=certifi.where())
db = client[os.getenv('MONGO_DB_NAME')]

# Clear original voters collection
voters_collection = db[os.getenv('MONGO_VOTERS_COLLECTION', 'voters')]
res_voters = voters_collection.delete_many({})
print(f"Deleted {res_voters.deleted_count} imported voters.")

# Generated Voters MongoDB (second cluster — all generation activity)
gen_uri = os.getenv('GEN_MONGO_URI')
if gen_uri:
    gen_client = MongoClient(gen_uri, tlsCAFile=certifi.where())
    gen_db = gen_client[os.getenv('GEN_MONGO_DB_NAME', 'generated_voters')]
    gen_col = gen_db[os.getenv('GEN_MONGO_COLLECTION', 'generated_voters')]
    stats_col = gen_db[os.getenv('MONGO_STATS_COLLECTION', 'generation_stats')]
    otp_col = gen_db['otp_sessions']
    verified_col = gen_db['verified_mobiles']

    res_gen = gen_col.delete_many({})
    print(f"Deleted {res_gen.deleted_count} generated voters.")

    res_stats = stats_col.delete_many({})
    print(f"Deleted {res_stats.deleted_count} generation stats.")

    res_otp = otp_col.delete_many({})
    print(f"Deleted {res_otp.deleted_count} OTP records.")

    res_verified = verified_col.delete_many({})
    print(f"Deleted {res_verified.deleted_count} verified mobile records.")

    # Volunteer & Booth Agent requests
    vol_col = gen_db['volunteer_requests']
    ba_col = gen_db['booth_agent_requests']
    res_vol = vol_col.delete_many({})
    print(f"Deleted {res_vol.deleted_count} volunteer requests.")
    res_ba = ba_col.delete_many({})
    print(f"Deleted {res_ba.deleted_count} booth agent requests.")
else:
    print("No GEN_MONGO_URI set, skipping generated voters DB.")

# Reset local generation_stats.json
import json
stats_file = os.path.join(os.path.dirname(__file__), 'data', 'generation_stats.json')
if os.path.exists(stats_file):
    with open(stats_file, 'w') as f:
        json.dump({}, f)
    print("Reset local generation_stats.json.")

print("Reset complete.")
