import os

def _load_local_gifs(directory: str) -> list[str]:
    """Load all files from a directory, excluding .gitkeep, prepended with $local::
    This helper will create the folder if it doesn't exist on the filesystem."""
    
    files = []
    if os.path.exists(directory):
        for filename in os.listdir(directory):
            if filename != '.gitkeep' and os.path.isfile(os.path.join(directory, filename)):
                files.append(f"$local::{os.path.join(directory, filename)}")
    else:
        os.makedirs(directory, exist_ok=True)
    
    return files
