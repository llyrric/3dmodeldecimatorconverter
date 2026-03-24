import bpy
import os
import sys
import argparse

def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)

def import_model(filepath):
    """
    Imports a 3D model based on its extension.
    Returns the imported object.
    """
    ext = os.path.splitext(filepath)[1].lower()
    
    # Deselect all before import to easily find the new object
    bpy.ops.object.select_all(action='DESELECT')
    
    if ext == '.obj':
        bpy.ops.wm.obj_import(filepath=filepath)
    elif ext == '.fbx':
        bpy.ops.import_scene.fbx(filepath=filepath)
    elif ext in ['.gltf', '.glb']:
        bpy.ops.import_scene.gltf(filepath=filepath)
    else:
        raise ValueError(f"Unsupported file format: {ext}")
    
    # Selection should contain the imported objects
    imported_objs = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
    
    if not imported_objs:
        # Sometimes objects aren't selected after import, try to finding them
        imported_objs = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
        
    if not imported_objs:
        raise RuntimeError("No mesh found in imported file.")
        
    # Join multiple meshes into one for easier processing if necessary
    if len(imported_objs) > 1:
        bpy.ops.object.select_all(action='DESELECT')
        for obj in imported_objs:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = imported_objs[0]
        bpy.ops.object.join()
        high_poly = bpy.context.active_object
    else:
        high_poly = imported_objs[0]
        
    high_poly.name = "HighPoly"
    
    # Ensure high poly has at least one material for baking (albedo/color)
    if not high_poly.data.materials:
        mat = bpy.data.materials.new(name="HighPolyDefault")
        high_poly.data.materials.append(mat)
    else:
        mat = high_poly.data.materials[0]
        
    if not mat.use_nodes:
        mat.use_nodes = True
        
    # Set a light grey color if it's the default material
    if mat.name == "HighPolyDefault":
        nodes = mat.node_tree.nodes
        principled = nodes.get("Principled BSDF")
        if principled:
            principled.inputs['Base Color'].default_value = (0.7, 0.7, 0.7, 1.0)
        
    return high_poly

def decimate_mesh(obj, target_triangles):
    """
    Decimates a mesh to reach a target triangle count.
    """
    # Count current triangles
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh_eval = obj.evaluated_get(depsgraph).data
    current_triangles = len(mesh_eval.polygons) # This is a rough count, but we'll use it
    
    # If the mesh has quads/ngons, we should triangulate first to get accurate counts
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_add(type='TRIANGULATE')
    bpy.ops.object.modifier_apply(modifier="Triangulate")
    
    current_triangles = len(obj.data.polygons)
    print(f"Current triangle count: {current_triangles}")
    
    if current_triangles <= target_triangles:
        print("Model is already below target triangle count. Skipping decimation.")
        return 1.0
        
    ratio = target_triangles / current_triangles
    print(f"Applying decimation with ratio: {ratio:.4f}")
    
    modifier = obj.modifiers.new(name="Decimate", type='DECIMATE')
    modifier.ratio = ratio
    modifier.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier="Decimate")
    
    return ratio

def prepare_low_poly(high_poly, target_triangles):
    """
    Duplicates high poly, decimates it, and unwraps UVs.
    """
    # Duplicate
    bpy.ops.object.select_all(action='DESELECT')
    high_poly.select_set(True)
    bpy.context.view_layer.objects.active = high_poly
    bpy.ops.object.duplicate()
    
    low_poly = bpy.context.active_object
    low_poly.name = "LowPoly"
    
    # Decimate
    decimate_mesh(low_poly, target_triangles)
    
    # UV Unwrap
    print("Unwrapping UVs...")
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    # Use Smart Project for robust automatic UVs
    bpy.ops.uv.smart_project(angle_limit=1.15192, margin_method='SCALED', island_margin=0.01)
    bpy.ops.object.mode_set(mode='OBJECT')
    
    return low_poly

def setup_baking_material(obj, width, height):
    """
    Sets up a material with a texture node for baking.
    """
    mat = bpy.data.materials.new(name="BakeMaterial")
    mat.use_nodes = True
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    
    # Clear existing nodes
    for node in nodes:
        nodes.remove(node)
        
    # Standard PBR nodes
    node_principled = nodes.new('ShaderNodeBsdfPrincipled')
    node_output = nodes.new('ShaderNodeOutputMaterial')
    links.new(node_principled.outputs['BSDF'], node_output.inputs['Surface'])
    
    # Texture node for baking
    bake_tex = nodes.new('ShaderNodeTexImage')
    bake_tex.name = "BAKE_TARGET"
    bake_tex.location = (-300, 0)
    
    # We'll create the actual image in the bake loop
    return mat, bake_tex

def bake_and_save(high_poly, low_poly, bake_type, image_name, res_w, res_h, output_path):
    """
    Bakes a specific pass and saves it.
    """
    print(f"Baking {bake_type}...")
    
    # Create image
    img = bpy.data.images.new(image_name, width=res_w, height=res_h)
    
    # Ensure low poly has the bake material and texture node active
    mat = low_poly.data.materials[0]
    bake_node = mat.node_tree.nodes["BAKE_TARGET"]
    bake_node.image = img
    mat.node_tree.nodes.active = bake_node
    
    # Selection setup: High selected, Low active
    bpy.ops.object.select_all(action='DESELECT')
    high_poly.select_set(True)
    low_poly.select_set(True)
    bpy.context.view_layer.objects.active = low_poly
    
    # Bake settings
    bpy.context.scene.render.engine = 'CYCLES'
    # Use CPU by default if GPU fails or not available, but try to use GPU
    try:
        bpy.context.scene.cycles.device = 'GPU'
    except:
        bpy.context.scene.cycles.device = 'CPU'
        
    bpy.context.scene.render.bake.use_selected_to_active = True
    bpy.context.scene.render.bake.margin = 16
    bpy.context.scene.render.bake.cage_extrusion = 0.2 # Increased for complex minis
    bpy.context.scene.render.bake.use_clear = True
    bpy.context.scene.render.bake.use_clear = True
    
    # Configure pass settings for Diffuse to avoid black shadows
    if bake_type == 'DIFFUSE':
        bpy.context.scene.render.bake.use_pass_direct = False
        bpy.context.scene.render.bake.use_pass_indirect = False
        bpy.context.scene.render.bake.use_pass_color = True
    
    # Perform bake
    bpy.ops.object.bake(type=bake_type)
    
    # Save image
    img.filepath_raw = output_path
    img.file_format = 'PNG'
    img.save()
    print(f"Saved {bake_type} bake to {output_path}")
    
    return img

def apply_baked_textures(low_poly, diffuse_img=None, normal_img=None):
    """
    Connects the baked textures to the material of the low poly model for preview/export.
    """
    mat = low_poly.data.materials[0]
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    principled = nodes.get('Principled BSDF')
    
    if not principled:
        # Try to find by type
        for n in nodes:
            if n.type == 'BSDF_PRINCIPLED':
                principled = n
                break
    
    if diffuse_img and principled:
        diff_node = nodes.new('ShaderNodeTexImage')
        diff_node.image = diffuse_img
        links.new(diff_node.outputs['Color'], principled.inputs['Base Color'])
        
    if normal_img and principled:
        norm_node = nodes.new('ShaderNodeTexImage')
        norm_node.image = normal_img
        norm_node.image.colorspace_settings.name = 'Non-Color'
        
        norm_map = nodes.new('ShaderNodeNormalMap')
        links.new(norm_node.outputs['Color'], norm_map.inputs['Color'])
        links.new(norm_map.outputs['Normal'], principled.inputs['Normal'])

def main():
    # Parse arguments after '--'
    if "--" not in sys.argv:
        args_list = []
    else:
        args_list = sys.argv[sys.argv.index("--") + 1:]
        
    parser = argparse.ArgumentParser(description="Blender Model Processor")
    parser.add_argument("--input", required=True, help="Path to high poly model")
    parser.add_argument("--output", required=True, help="Path for output model")
    parser.add_argument("--format", default="obj", choices=["obj", "fbx", "glb"], help="Output format")
    parser.add_argument("--triangles", type=int, default=20000, help="Target triangle count")
    parser.add_argument("--resolution", type=int, default=2048, help="Texture resolution")
    parser.add_argument("--bake_diffuse", action="store_true", help="Bake diffuse color")
    parser.add_argument("--bake_normal", action="store_true", help="Bake normal map")
    parser.add_argument("--bake_roughness", action="store_true", help="Bake roughness map")
    parser.add_argument("--bake_metallic", action="store_true", help="Bake metallic map")
    
    args = parser.parse_args(args_list)
    
    clear_scene()
    
    print(f"Processing: {args.input}")
    high_poly = import_model(args.input)
    
    print(f"Decimating to {args.triangles} triangles...")
    low_poly = prepare_low_poly(high_poly, args.triangles)
    
    # Setup rendering context
    bpy.context.scene.render.engine = 'CYCLES'
    
    # Output directory handling
    output_dir = os.path.dirname(args.output)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        
    # Use a temporary directory for baking if format embeds textures
    import tempfile
    import shutil
    
    if args.format != 'obj':
        bake_dir_obj = tempfile.TemporaryDirectory()
        bake_dir = bake_dir_obj.name
    else:
        bake_dir = output_dir
        
    setup_baking_material(low_poly, args.resolution, args.resolution)
    
    diff_img = None
    norm_img = None
    
    if args.bake_diffuse:
        diff_path = os.path.join(bake_dir, "diffuse.png")
        diff_img = bake_and_save(high_poly, low_poly, 'DIFFUSE', "DiffuseBake", args.resolution, args.resolution, diff_path)
        
    if args.bake_normal:
        norm_path = os.path.join(bake_dir, "normal.png")
        norm_img = bake_and_save(high_poly, low_poly, 'NORMAL', "NormalBake", args.resolution, args.resolution, norm_path)
        
    if args.bake_roughness:
        rough_path = os.path.join(bake_dir, "roughness.png")
        bake_and_save(high_poly, low_poly, 'ROUGHNESS', "RoughnessBake", args.resolution, args.resolution, rough_path)
        
    if args.bake_metallic:
        metal_path = os.path.join(bake_dir, "metallic.png")
        bake_and_save(high_poly, low_poly, 'GLOSSY', "MetallicBake", args.resolution, args.resolution, metal_path)
        
    # Apply textures for formats that pack them (GLB) or for visual correctness
    apply_baked_textures(low_poly, diff_img, norm_img)
    
    # Hide high poly (don't delete it yet, maybe keep it for reference)
    high_poly.hide_viewport = True
    high_poly.hide_render = True
    
    # Export
    print(f"Exporting to {args.format} at: {args.output}")
    bpy.ops.object.select_all(action='DESELECT')
    low_poly.select_set(True)
    bpy.context.view_layer.objects.active = low_poly
    
    try:
        if args.format == 'obj':
            bpy.ops.wm.obj_export(filepath=args.output, export_selected_objects=True)
        elif args.format == 'fbx':
            bpy.ops.export_scene.fbx(filepath=args.output, use_selection=True)
        elif args.format == 'glb':
            # For GLB, ensure textures are included
            print("Using GLB format")
            # Explicit parameters to be safe
            bpy.ops.export_scene.gltf(
                filepath=args.output, 
                export_format='GLB', 
                use_selection=True,
                export_image_format='AUTO'
            )
        
        # Verify file creation
        if os.path.exists(args.output):
            print(f"File successfully created: {args.output}")
            size_mb = os.path.getsize(args.output) / (1024 * 1024)
            print(f"Output size: {size_mb:.2f} MB")
        else:
            print(f"ERROR: Export operator completed but no file found at: {args.output}")
            
    except Exception as e:
        print(f"ERROR during export: {str(e)}")
        import traceback
        traceback.print_exc()
        
    # Cleanup temp dir
    if args.format != 'obj':
        bake_dir_obj.cleanup()
        
    print("Processing complete!")

if __name__ == "__main__":
    main()
