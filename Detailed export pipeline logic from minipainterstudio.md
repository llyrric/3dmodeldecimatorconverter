# Mini Painter Studio: Detailed Export Pipeline Logic

To successfully build a polygon decimator for **Mini Painter Studio**, the following details regarding the exporter's architecture, attribute encoding, and auxiliary tool data must be understood.

## 1. Export Format & Infrastructure
- **Format**: GLB (Binary) or GLTF (JSON), following the **glTF 2.0** specification.
- **Engine**: Powered by Three.js `GLTFExporter`.
- **Lighting Model**: Standard PBR (Physically Based Rendering) using the Metallic-Roughness workflow.

## 2. Geometry Preparation (The "Bake" Phase)
Before serialization, the model undergoes several critical transformations:
- **World Transform Baking**: All rotations, scales, and positions are applied directly to the vertex `position` attribute using `geometry.applyMatrix4(mesh.matrixWorld)`. The exported GLB node transform is reset to identity.
- **Normal Transformation**: Surface normals are recalculated to account for the world transform, ensuring lighting consistency.
- **Attribute Stripping**: Internal calculation layers used for real-time cavity/edge rendering are stripped to maximize compatibility: `selection`, `curvature`, `crevice`, and `edgeGradient`.

## 3. Vertex Attribute Encoding
The exporter preserves two primary non-standard attributes. A decimator **MUST** linearly interpolate these values during vertex collapses:

| Attribute | Type | Description |
| :--- | :--- | :--- |
| `color` | `vec3` | **Vertex Colors**: SRGB primary paint layer. |
| `_SURFACE_PROPS` | `vec3` | **Custom PBR Data**: (X: Roughness, Y: Metalness, Z: Sheen). |

> [!IMPORTANT]
> The `_SURFACE_PROPS` attribute is the "source of truth" for material finishes. Metalness > 0.5 is treated as metallic; Sheen > 0.4 is treated as satin/fabric.

## 4. Texture Baking (Standard Workflow)
For models with UV coordinates, the exporter generates 2048x2048 textures to "bake in" the procedural vertex data:
- **Diffuse Map**: Generated from the `color` vertex attribute.
- **ORM Map**: A packed texture where **Red = AO** (default 1.0), **Green = Roughness** (`_SURFACE_PROPS.x`), and **Blue = Metalness** (`_SURFACE_PROPS.y`).
- **Sheen Map**: Sourced from `_SURFACE_PROPS.z` and exported via the `KHR_materials_sheen` GLTF extension.
- **Seam Dilation**: A 4-pixel dilation pass is applied to all maps to prevent texture bleeding at UV island edges.

## 5. Fallback: Geometry Shredding (No UVs)
For models without UVs (e.g., STL imports), the exporter uses "shredder" logic:
- The mesh is split into separate sub-meshes based on its `_SURFACE_PROPS`.
- Every triangle is assigned to one of four material buckets: `matte`, `satin`, `gloss`, or `metal`.
- **Decimator Impact**: If decimation creates new triangles spanning two material "zones", the decimator should prioritize preserving the boundary edges to prevent material bleeding.

## 6. Decals (Eye Tool, Sticker Tool)
Tools that place decals (like the **Eye Tool**) do not modify the vertex data of the base model. Instead, they create separate `sticker` meshes:
- **Z-Fighting Prevention**: Decals are offset by `0.005` units along their surface normal.
- **Export Structure**: These are exported as child meshes under the main model node.
- **Decimator Recommendation**: Decals should be decimated separately from the base mesh to preserve their specific UV mapping and transparency layers.

## 7. Decimator Integration Checklist
- [ ] **Preserve `_SURFACE_PROPS`**: Do not discard this attribute; it is required for re-importing models for further painting.
- [ ] **Weighted Interpolation**: Use vertex-weighted interpolation for both `color` and `_SURFACE_PROPS` during edge collapses.
- [ ] **Maintain UV Seams**: Continuity at seams is vital to avoid warping the ORM/Color maps.
- [ ] **Handle Hierarchy**: Ensure the child decal meshes are preserved and their relative offsets are maintained.
