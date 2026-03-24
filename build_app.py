import subprocess
import sys
import os

def build():
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Build command
    # Use --onedir for better performance since Blender is large
    # Include processor.py
    cmd = [
        "pyinstaller",
        "--noconsole",
        "--onedir",
        "--add-data", "processor.py;.",
        "--name", "3D_Decimator_and_Baker",
        "main.py"
    ]

    print("Building application (this may take a minute)...")
    subprocess.run(cmd)

    print("\nBUILD COMPLETE!")
    print("Next steps:")
    print("1. Locate the 'dist/3D_Decimator_and_Baker' folder.")
    print("2. Download Portable Blender (.zip) from blender.org.")
    print("3. Extract it and rename the folder to 'blender'.")
    print("4. Move the 'blender' folder INTO the 'dist/3D_Decimator_and_Baker' directory.")
    print("5. You can now distribute the entire '3D_Decimator_and_Baker' folder.")

if __name__ == "__main__":
    build()
