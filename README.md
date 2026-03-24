# 3D Model Decimator & Baker

A modern, easy-to-use tool to decimate high-poly 3D models into low-poly versions (targeting sub-20,000 triangles) while preserving detail through baked texture maps.

## Features

- **Mesh Decimation**: Powered by Blender's engine for high-quality geometry reduction.
- **Advanced Baking**: Projects Normal, Diffuse, Metallic, and Roughness maps from high-poly to low-poly.
- **Multiple Formats**: Supports `.obj`, `.fbx`, and `.glb`.
- **Game-Ready Outputs**: Standardized for Tabletop Simulator and TaleSpire.
- **Bundled Engine Support**: Can be built as a standalone executable.

## Download & Run (Standalone)

For the easiest experience, you can download the pre-built standalone version which **includes** the Blender engine:

1.  Go to the [Releases](https://github.com/llyrric/3dmodeldecimatorconverter/releases) page.
2.  Download the latest `3D_Decimator_vX.X.zip` file.
3.  Extract the ZIP file to a folder of your choice.
4.  Run `3D_Decimator_and_Baker.exe` inside the extracted folder.

*No Blender installation or Python setup is required for this version.*

## How to Run (Local Python)

1.  **Install Requirements**:
    ```bash
    pip install PySide6
    ```
2.  **Install Blender**: Ensure [Blender](https://www.blender.org/download/) is installed on your system.
3.  **Launch**:
    ```bash
    python main.py
    ```

## How to Build the Standalone Executable

1.  **Download Portable Blender**: Get the **Windows Portable (.zip)** version from [blender.org](https://www.blender.org/download/lts/4-2/).
2.  **Run Build Script**:
    ```bash
    python build_app.py
    ```
3.  **Finalize Bundle**:
    - Locate the `dist/3D_Decimator_and_Baker` folder.
    - Extract Blender and rename its folder to `blender`.
    - Move the `blender` folder into the `dist/3D_Decimator_and_Baker` directory.

## Requirements

- Python 3.10+
- PySide6
- Blender 4.2+ (Recommended)
