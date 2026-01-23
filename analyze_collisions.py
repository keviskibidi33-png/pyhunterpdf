
import re
from pathlib import Path
from collections import Counter
from urllib.parse import urlparse, unquote

INPUT_FILE = Path(r"c:\Users\Lenovo\Desktop\pdfinacal\linkpdfinacal.txt")

def analyze():
    print(f"Reading {INPUT_FILE}...")
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f if l.strip()]
    except UnicodeDecodeError:
        with open(INPUT_FILE, 'r', encoding='latin-1') as f:
            lines = [l.strip() for l in f if l.strip()]
            
    total_links = len(lines)
    unique_links = len(set(lines))
    
    print(f"Total links: {total_links}")
    print(f"Unique links: {unique_links}")
    
    if total_links != unique_links:
        print(f"⚠️  WARNING: Found {total_links - unique_links} duplicate lines in the source file!")
        
    # Analyze Slugs
    print("\nAnalyzing Slugs (potential filenames)...")
    slugs = []
    for url in lines:
        # Logic from main.py: url_slug = str(response.url).split('/')[-1].split('?')[0]
        # We simulate this using the input URL. Note: main.py uses response.url (after redirect)
        # but this is the best static analysis we can do.
        
        try:
            parsed = urlparse(url)
            path = unquote(parsed.path)
            slug = path.split('/')[-1]
            if not slug: # Handle trailing slash
                slug = path.split('/')[-2]
            
            # Clean invalid chars like main.py
            slug = re.sub(r'[\\/*?:"<>|]', '_', slug)
            
            # Simulated filename logic from main.py
            # If slug > 3 chars, it uses slug.
            if len(slug) > 3:
                slugs.append(slug)
            else:
                slugs.append("documento.pdf") # Fallback in main.py
                
        except Exception:
            slugs.append("ERROR_PARSING")

    slug_counts = Counter(slugs)
    collisions = {k: v for k, v in slug_counts.items() if v > 1}
    
    print(f"Total projected filenames: {len(slugs)}")
    print(f"Unique projected filenames: {len(slug_counts)}")
    print(f"Potential Collisions: {len(collisions)}")
    
    collision_loss = total_links - len(slug_counts)
    print(f"Files that would be overwritten/lost: {collision_loss}")
    
    if collisions:
        print("\nTop 10 Collisions:")
        for name, count in sorted(collisions.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  {name}: {count} occurrences")
            
        # Write report
        with open("collision_report.txt", "w", encoding="utf-8") as f:
            f.write(f"Total Links: {total_links}\n")
            f.write(f"Unique Filenames: {len(slug_counts)}\n")
            f.write(f"Lost Files: {collision_loss}\n\n")
            f.write("COLLISIONS:\n")
            for name, count in collisions.items():
                f.write(f"{name}: {count}\n")
        print("\nReport saved to collision_report.txt")

if __name__ == "__main__":
    analyze()
