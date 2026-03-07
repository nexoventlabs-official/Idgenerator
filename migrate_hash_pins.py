"""
PIN Migration Script
====================
Migrates existing plaintext PINs to hashed format.

IMPORTANT: Run this ONCE after deploying the security fixes.

Usage:
    python migrate_hash_pins.py --dry-run  # Preview changes
    python migrate_hash_pins.py            # Apply changes
"""
import os
import sys
import argparse
from dotenv import load_dotenv
from pymongo import MongoClient
import certifi

# Load environment
load_dotenv()

# Import hash function
from security_fixes import hash_pin


def migrate_pins(dry_run: bool = True):
    """Migrate plaintext PINs to hashed format."""
    
    # Connect to MongoDB
    gen_uri = os.getenv('GEN_MONGO_URI')
    if not gen_uri:
        print("ERROR: GEN_MONGO_URI not found in .env")
        sys.exit(1)
    
    client = MongoClient(gen_uri, tlsCAFile=certifi.where())
    db = client[os.getenv('GEN_MONGO_DB_NAME', 'generated_voters')]
    
    stats_col = db[os.getenv('MONGO_STATS_COLLECTION', 'generation_stats')]
    gen_voters_col = db[os.getenv('GEN_MONGO_COLLECTION', 'generated_voters')]
    
    print("=" * 60)
    print("PIN MIGRATION SCRIPT")
    print("=" * 60)
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE (will update database)'}")
    print()
    
    # Migrate stats_col
    print("Checking generation_stats collection...")
    stats_docs = list(stats_col.find({'secret_pin': {'$exists': True, '$ne': None}}))
    stats_to_migrate = []
    
    for doc in stats_docs:
        pin = doc.get('secret_pin', '')
        if pin and '$' not in pin:  # Not already hashed
            stats_to_migrate.append(doc)
    
    print(f"Found {len(stats_to_migrate)} PINs to migrate in generation_stats")
    
    if not dry_run and stats_to_migrate:
        for doc in stats_to_migrate:
            pin = doc['secret_pin']
            hashed = hash_pin(pin)
            stats_col.update_one(
                {'_id': doc['_id']},
                {'$set': {'secret_pin': hashed}}
            )
        print(f"✓ Migrated {len(stats_to_migrate)} PINs in generation_stats")
    
    # Migrate gen_voters_col
    print("\nChecking generated_voters collection...")
    gen_docs = list(gen_voters_col.find({'secret_pin': {'$exists': True, '$ne': None}}))
    gen_to_migrate = []
    
    for doc in gen_docs:
        pin = doc.get('secret_pin', '')
        if pin and '$' not in pin:  # Not already hashed
            gen_to_migrate.append(doc)
    
    print(f"Found {len(gen_to_migrate)} PINs to migrate in generated_voters")
    
    if not dry_run and gen_to_migrate:
        for doc in gen_to_migrate:
            pin = doc['secret_pin']
            hashed = hash_pin(pin)
            gen_voters_col.update_one(
                {'_id': doc['_id']},
                {'$set': {'secret_pin': hashed}}
            )
        print(f"✓ Migrated {len(gen_to_migrate)} PINs in generated_voters")
    
    print()
    print("=" * 60)
    if dry_run:
        print("DRY RUN COMPLETE - No changes made")
        print("Run without --dry-run to apply changes")
    else:
        print("MIGRATION COMPLETE")
        print(f"Total PINs migrated: {len(stats_to_migrate) + len(gen_to_migrate)}")
    print("=" * 60)
    
    client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Migrate plaintext PINs to hashed format')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without applying')
    args = parser.parse_args()
    
    migrate_pins(dry_run=args.dry_run)
