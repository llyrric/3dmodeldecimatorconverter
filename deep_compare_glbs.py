import json
import struct
import os

def analyze_glb(path):
    if not os.path.exists(path):
        return None
    print(f"\nAnalyzing: {path}")
    with open(path, 'rb') as f:
        magic = f.read(4)
        if magic != b'glTF':
            print("Not a GLB file")
            return None
        version = struct.unpack('<I', f.read(4))[0]
        length = struct.unpack('<I', f.read(4))[0]
        
        # JSON Chunk
        c_len = struct.unpack('<I', f.read(4))[0]
        c_type = f.read(4)
        data = json.loads(f.read(c_len).decode('utf-8'))
        
    print(f" - Version: {version}")
    print(f" - Extensions Used: {data.get('extensionsUsed', [])}")
    print(f" - Extensions Required: {data.get('extensionsRequired', [])}")
    
    for i, mesh in enumerate(data.get('meshes', [])):
        print(f" - Mesh {i}: {mesh.get('name')}")
        for j, prim in enumerate(mesh.get('primitives', [])):
            print(f"   - Primitive {j} attributes: {list(prim.get('attributes', {}).keys())}")
            if prim.get('extensions'):
                print(f"   - Primitive extensions: {list(prim['extensions'].keys())}")
    
    print(f" - Materials: {len(data.get('materials', []))}")
    for mat in data.get('materials', []):
        print(f"   - Material: {mat.get('name')}")
        pbr = mat.get('pbrMetallicRoughness', {})
        print(f"     - pbr: {pbr}")
        if mat.get('extensions'):
             print(f"     - extensions: {list(mat['extensions'].keys())}")
    
    return data

input_file = r"C:\Users\llyrr\OneDrive\Documents\web apps\3d mdoel decimator and converter\models\tieflingglbtest3.glb"
output_file = r"C:\Users\llyrr\OneDrive\Documents\web apps\3d mdoel decimator and converter\models\tieflingglbtest3_low.glb"

in_data = analyze_glb(input_file)
out_data = analyze_glb(output_file)
