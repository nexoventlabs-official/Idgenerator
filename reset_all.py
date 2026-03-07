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

# ── MongoDB 1 — Voter data (primary cluster) ──
uri = os.getenv('MONGO_URI')
db_name = os.getenv('MONGO_DB_NAME')
client = MongoClient(uri, tlsCAFile=certifi.where())
db = client[db_name]

# Drop voters collection (frees storage completely)
voters_col_name = os.getenv('MONGO_VOTERS_COLLECTION', 'voters')
if voters_col_name in db.list_collection_names():
    db.drop_collection(voters_col_name)
    print(f"Dropped collection '{voters_col_name}' — storage freed.")
else:
    print(f"Collection '{voters_col_name}' not found, skipping.")

# Show DB1 storage after reset
try:
    stats = db.command('dbstats')
    size_mb = round(stats.get('storageSize', 0) / (1024 * 1024), 2)
    data_mb = round(stats.get('dataSize', 0) / (1024 * 1024), 2)
    print(f"[DB1: {db_name}] Storage: {size_mb} MB, Data: {data_mb} MB")
except Exception as e:
    print(f"Could not get DB1 stats: {e}")

# ── MongoDB 2 — Generated voters (second cluster) ──
gen_uri = os.getenv('GEN_MONGO_URI')
if gen_uri:
    gen_db_name = os.getenv('GEN_MONGO_DB_NAME', 'generated_voters')
    gen_client = MongoClient(gen_uri, tlsCAFile=certifi.where())
    gen_db = gen_client[gen_db_name]

    # Drop all collections in the generated voters database
    collections_to_drop = [
        os.getenv('GEN_MONGO_COLLECTION', 'generated_voters'),
        os.getenv('MONGO_STATS_COLLECTION', 'generation_stats'),
        'otp_sessions',
        'verified_mobiles',
        'volunteer_requests',
        'booth_agent_requests',
    ]
    for col_name in collections_to_drop:
        if col_name in gen_db.list_collection_names():
            gen_db.drop_collection(col_name)
            print(f"Dropped collection '{col_name}'")
        else:
            print(f"Collection '{col_name}' not found, skipping.")

    # Show DB2 storage after reset
    try:
        stats = gen_db.command('dbstats')
        size_mb = round(stats.get('storageSize', 0) / (1024 * 1024), 2)
        data_mb = round(stats.get('dataSize', 0) / (1024 * 1024), 2)
        print(f"[DB2: {gen_db_name}] Storage: {size_mb} MB, Data: {data_mb} MB")
    except Exception as e:
        print(f"Could not get DB2 stats: {e}")
else:
    print("No GEN_MONGO_URI set, skipping generated voters DB.")

# Reset local generation_stats.json
import json
stats_file = os.path.join(os.path.dirname(__file__), 'data', 'generation_stats.json')
if os.path.exists(stats_file):
    with open(stats_file, 'w') as f:
        json.dump({}, f)
    print("Reset local generation_stats.json.")

print("\n✅ Reset complete — both databases cleared and storage freed.")
