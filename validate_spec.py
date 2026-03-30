import bpy
import os
import sys
import argparse

def validate_glb(filepath):
    print(f"Validating GLB: {filepath}")
    
    # Import the model
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=filepath)
    
    obj = next((o for o in bpy.context.scene.objects if o.type == 'MESH'), None)
    if not obj:
        print("ERROR: No mesh found in exported GLB.")
        return False
        
    print(f"Mesh name: {obj.name}")
    print(f"Attributes: {', '.join([a.name for a in obj.data.attributes])}")
    
    # Check for SURFACE_PROPS
    sp_attr = next((a.name for a in obj.data.attributes if 'SURFACE_PROPS' in a.name.upper()), None)
    if sp_attr:
        print(f"SUCCESS: SURFACE_PROPS attribute preserved as {sp_attr}.")
    else:
        print("WARNING: SURFACE_PROPS attribute missing.")
        
    # Check material for ORM and Sheen
    if obj.data.materials:
        mat = obj.data.materials[0]
        print(f"Material: {mat.name}")
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        
        principled = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if principled:
            # Check ORM
            rough_input = principled.inputs['Roughness']
            metal_input = principled.inputs['Metallic']
            
            if rough_input.is_linked and metal_input.is_linked:
                node_r = rough_input.links[0].from_node
                node_m = metal_input.links[0].from_node
                
                if node_r.type == 'SEPARATE_COLOR' and node_m.type == 'SEPARATE_COLOR':
                    tex_node = node_r.inputs[0].links[0].from_node
                    if tex_node.type == 'TEX_IMAGE':
                        print(f"SUCCESS: ORM texture detected: {tex_node.image.name}")
            
            # Check Sheen
            sheen_input = principled.inputs.get('Sheen Weight') or principled.inputs.get('Sheen')
            if sheen_input and sheen_input.is_linked:
                tex_node = sheen_input.links[0].from_node
                if tex_node.type == 'TEX_IMAGE':
                    print(f"SUCCESS: Sheen texture detected: {tex_node.image.name}")
                    if 'sheen' in tex_node.image.name.lower():
                        print("SUCCESS: Sheen texture linked correctly.")
            else:
                print("INFO: Sheen not linked to textures.")
                
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: blender --background --python validate_spec.py -- <path_to_glb>")
    else:
        path = sys.argv[-1]
        validate_glb(path)
