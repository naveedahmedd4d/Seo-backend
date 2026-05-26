"""
Script to clean up duplicate WordPress sites
Run this to remove duplicate entries keeping only the most recent one per URL
"""
from app.database import SessionLocal
from app.models.wordpress_site import WordPressSite
from sqlalchemy import func

def cleanup_duplicates():
    db = SessionLocal()
    try:
        # Find duplicates by URL and user_id
        duplicates = db.query(
            WordPressSite.url,
            WordPressSite.user_id,
            func.count(WordPressSite.id).label('count')
        ).group_by(WordPressSite.url, WordPressSite.user_id).having(func.count(WordPressSite.id) > 1).all()
        
        print(f"Found {len(duplicates)} duplicate URL groups")
        
        total_deleted = 0
        for url, user_id, count in duplicates:
            # Get all sites with this URL and user_id, ordered by created_at (keep newest)
            sites = db.query(WordPressSite).filter(
                WordPressSite.url == url,
                WordPressSite.user_id == user_id
            ).order_by(WordPressSite.created_at.desc()).all()
            
            # Keep the first (newest) one, delete the rest
            to_delete = sites[1:]
            for site in to_delete:
                print(f"Deleting duplicate: ID {site.id}, Name: {site.name}, URL: {site.url}")
                db.delete(site)
                total_deleted += 1
        
        db.commit()
        print(f"\nCleanup complete! Deleted {total_deleted} duplicate entries.")
        
    except Exception as e:
        db.rollback()
        print(f"Error during cleanup: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    cleanup_duplicates()

