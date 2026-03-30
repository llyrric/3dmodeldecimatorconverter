import bpy
import os
import sys
import argparse
import struct
import json
import tempfile
import shutil
import zipfile

def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)

def clean_glb(filepath):
    """
    Unpacks GLB into a separate .gltf and .bin file to bypass Blender importer crashes.
    Also strips custom attributes, extras, animations, and skins.
    Returns the path to the temporary .gltf file.
    """
    if not filepath.lower().endswith('.glb'):
        return filepath
        
    try:
        base = filepath + ".unpacked"
        gltf_path = base + ".gltf"
        bin_filename = os.path.basename(base) + ".bin"
        bin_path = os.path.join(os.path.dirname(filepath), bin_filename)

        with open(filepath, 'rb') as f:
            # Header
            magic = f.read(4)
            if magic != b'glTF':
                return filepath
            version = struct.unpack('<I', f.read(4))[0]
            f.read(4) # total_length

            # JSON Chunk
            chunk_len = struct.unpack('<I', f.read(4))[0]
            chunk_type = f.read(4)
            if chunk_type != b'JSON':
                return filepath
            
            json_bytes = f.read(chunk_len).strip(b'\x00\x20')
            data = json.loads(json_bytes.decode('utf-8'))

            # BIN Chunk
            bin_chunk_len = struct.unpack('<I', f.read(4))[0]
            bin_type = f.read(4)
            if bin_type != b'BIN\x00':
                bin_data = b''
            else:
                bin_data = f.read(bin_chunk_len)
            
        modified = False
        def clean_recursive(obj, key=None):
            nonlocal modified
            if isinstance(obj, dict):
                to_remove = []
                for k in list(obj.keys()):
                    # Keep materials, textures, images, samplers for baking
                    # Still remove extras, animations, skins as they are crash-prone
                    # CRITICAL: Always remove COLOR_0 as it causes Blender 5.1 crashes
                    if k.startswith('_'):
                        # Remove all custom attributes starting with _
                        to_remove.append(k)
                    elif k == 'COLOR_0' and key == 'attributes':
                        to_remove.append(k)
                    elif k in ['extras', 'animations', 'skins', 'cameras']:
                        to_remove.append(k)
                    else:
                        clean_recursive(obj[k], k)
                if to_remove:
                    for k in to_remove:
                        del obj[k]
                    modified = True
            elif isinstance(obj, list):
                for item in obj:
                    clean_recursive(item)

        clean_recursive(data)
        
        # We no longer strip materials from prims here
        
        # Ensure buffers exist
        if 'buffers' not in data:
            data['buffers'] = [{}]
        
        # Update buffer reference to the external .bin file
        if 'buffers' in data and len(data['buffers']) > 0:
            data['buffers'][0]['uri'] = bin_filename
            data['buffers'][0]['byteLength'] = len(bin_data)

        print(f"Unpacked and cleaned GLB to {os.path.basename(gltf_path)}")
        
        with open(gltf_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
            
        if bin_data:
            with open(bin_path, 'wb') as f:
                f.write(bin_data)
                
        return gltf_path
        
    except Exception as e:
        print(f"Warning: Could not unpack/clean GLB: {e}")
        import traceback
        traceback.print_exc()
        return filepath

def import_model(filepath):
    """
    Imports a 3D model based on its extension.
    Returns the imported object.
    """
    ext = os.path.splitext(filepath)[1].lower()
    
    # Deselect all before import to easily find the new object
    bpy.ops.object.select_all(action='DESELECT')
    
    clean_path = filepath
    try:
        if ext == '.obj':
            bpy.ops.wm.obj_import(filepath=filepath)
        elif ext == '.fbx':
            bpy.ops.import_scene.fbx(filepath=filepath)
        elif ext in ['.gltf', '.glb']:
            if ext == '.glb':
                clean_path = clean_glb(filepath)
            
            # Revert to default settings now that clean_glb handles the attribute issue
            # This is safer for Blender 5.1's internal state
            print(f"Starting GLTF import for: {clean_path}")
            bpy.ops.import_scene.gltf(filepath=clean_path)
            print("GLTF import operator finished."); sys.stdout.flush()
            
            # Force an update of the dependency graph and view layer
            # This can prevent access violations when accessing data immediately after import
            print("Updating view layer...")
            bpy.context.view_layer.update()
            print("View layer updated."); sys.stdout.flush()
            
        # Selection should contain the imported objects
        print("Checking selected objects...")
        imported_objs = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
        print(f"Found {len(imported_objs)} mesh objects in selection."); sys.stdout.flush()
        
        if not imported_objs:
            # Fallback: Find any mesh that wasn't there before
            print("No mesh in selection, falling back to finding all meshes...")
            imported_objs = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH' and obj.name != "HighPoly"]
            print(f"Found {len(imported_objs)} mesh objects in scene."); sys.stdout.flush()
            
        if not imported_objs:
            raise RuntimeError("No mesh found in imported file.")
            
        # Join multiple meshes into one for easier processing if necessary
        if len(imported_objs) > 1:
            print(f"Joining {len(imported_objs)} meshes...")
            bpy.ops.object.select_all(action='DESELECT')
            for obj in imported_objs:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = imported_objs[0]
            bpy.ops.object.join()
            high_poly = bpy.context.active_object
            print("Meshes joined."); sys.stdout.flush()
            bpy.context.view_layer.update()
        else:
            high_poly = imported_objs[0]
            
        high_poly.name = "HighPoly"
        
        # Ensure high poly has at least one material for baking (albedo/color)
        mat = None
        if hasattr(high_poly.data, 'materials') and len(high_poly.data.materials) > 0:
            # Check if the first slot actually has a material object
            if high_poly.data.materials[0] is not None:
                mat = high_poly.data.materials[0]
        
        if mat is None:
            mat = bpy.data.materials.new(name="HighPolyDefault")
            if len(high_poly.data.materials) == 0:
                high_poly.data.materials.append(mat)
            else:
                high_poly.data.materials[0] = mat
            
        # Skip material node configuration as it's known to be unstable in Blender 5.1
        # and we'll be setting up a baking material later anyway.
        return high_poly
        
    finally:
        # Cleanup cleaned file if one was created
        if clean_path != filepath and os.path.exists(clean_path):
            try:
                os.remove(clean_path)
                # Also remove the .bin file if it exists
                bin_path = clean_path.replace(".gltf", ".bin")
                if os.path.exists(bin_path):
                    os.remove(bin_path)
            except: pass

def decimate_mesh(obj, target_triangles):
    """
    Decimates a mesh to reach a target triangle count.
    """
    # Count current triangles
    print("Evaluating mesh for triangle count..."); sys.stdout.flush()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh_eval = obj.evaluated_get(depsgraph).data
    current_triangles = len(mesh_eval.polygons)
    print(f"Current polygons: {current_triangles}"); sys.stdout.flush()
    
    # If the mesh has quads/ngons, we should triangulate first to get accurate counts
    print("Triangulating..."); sys.stdout.flush()
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_add(type='TRIANGULATE')
    bpy.ops.object.modifier_apply(modifier="Triangulate")
    print("Triangulation applied."); sys.stdout.flush()
    
    current_triangles = len(obj.data.polygons)
    print(f"Current triangle count: {current_triangles}"); sys.stdout.flush()
    
    if current_triangles <= target_triangles:
        print("Model is already below target triangle count. Skipping decimation.")
        return 1.0
        
    ratio = target_triangles / current_triangles
    print(f"Applying decimation with ratio: {ratio:.4f}")
    
    modifier = obj.modifiers.new(name="Decimate", type='DECIMATE')
    modifier.ratio = ratio
    modifier.use_collapse_triangulate = True
    print("Applying decimation modifier..."); sys.stdout.flush()
    bpy.ops.object.modifier_apply(modifier="Decimate")
    print("Decimation applied."); sys.stdout.flush()
    bpy.context.view_layer.update()
    
    return ratio

def prepare_low_poly(high_poly, target_triangles):
    """
    Duplicates high poly, decimates it, and unwraps UVs.
    """
    print("Duplicating high poly..."); sys.stdout.flush()
    bpy.ops.object.select_all(action='DESELECT')
    high_poly.select_set(True)
    bpy.context.view_layer.objects.active = high_poly
    bpy.ops.object.duplicate()
    bpy.context.view_layer.update()
    
    low_poly = bpy.context.active_object
    low_poly.name = "LowPoly"
    print("Mesh duplicated. Cleaning low poly for decimation..."); sys.stdout.flush()
    
    # Aggressively clean low poly to prevent crashes during decimation/mode switch
    for uv in list(low_poly.data.uv_layers):
        low_poly.data.uv_layers.remove(uv)
    for col in list(low_poly.data.color_attributes):
        low_poly.data.color_attributes.remove(col)
    
    bpy.context.view_layer.update()
    print("Low poly cleaned."); sys.stdout.flush()
    
    # Decimate
    decimate_mesh(low_poly, target_triangles)
    
    # UV Unwrap
    print("Preparing for UV unwrap..."); sys.stdout.flush()
    bpy.context.view_layer.update()
    
    print("Switching to EDIT mode..."); sys.stdout.flush()
    bpy.ops.object.mode_set(mode='EDIT')
    print("Entered EDIT mode."); sys.stdout.flush()
    
    bpy.ops.mesh.select_all(action='SELECT')
    # Use Smart Project for robust automatic UVs
    print("Running Smart UV Project..."); sys.stdout.flush()
    bpy.ops.uv.smart_project(angle_limit=1.15192, margin_method='SCALED', island_margin=0.01)
    print("UV Project finished."); sys.stdout.flush()
    
    # Deselect all BEFORE switching back to Object mode
    print("Deselecting and returning to OBJECT mode..."); sys.stdout.flush()
    bpy.ops.mesh.select_all(action='DESELECT')
    
    # DO NOT call view_layer.update() here as it seems to trigger the crash in 5.1
    bpy.ops.object.mode_set(mode='OBJECT')
    print("Returned to OBJECT mode."); sys.stdout.flush()
    
    return low_poly

def setup_baking_material(obj, width, height):
    """
    Sets up a material with a texture node for baking.
    """
    print("Setting up baking material..."); sys.stdout.flush()
    mat = bpy.data.materials.new(name="BakeMaterial")
    print("New material created."); sys.stdout.flush()
    # NOTE: In Blender 5.1, we'll be very careful with use_nodes
    try:
        mat.use_nodes = True
        print("use_nodes set to True."); sys.stdout.flush()
    except Exception as e:
        print(f"Error setting use_nodes: {e}")
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
    
    print(f"Processing: {args.input}"); sys.stdout.flush()
    high_poly = import_model(args.input)
    print("Import model returned."); sys.stdout.flush()
    
    print(f"Decimating to {args.triangles} triangles..."); sys.stdout.flush()
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
        
    print("Preparing baking setup..."); sys.stdout.flush()
    setup_baking_material(low_poly, args.resolution, args.resolution)
    print("Baking material setup complete."); sys.stdout.flush()
    
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
    
    # If OBJ, we'll export to the same bake_dir to keep things together temporarily
    export_path = args.output
    if args.format == 'obj':
        export_path = os.path.join(bake_dir, os.path.basename(args.output))
        
    # Export
    print(f"Exporting to {args.format} at: {export_path}"); sys.stdout.flush()
    bpy.ops.object.select_all(action='DESELECT')
    low_poly.select_set(True)
    bpy.context.view_layer.objects.active = low_poly
    
    try:
        if args.format == 'obj':
            bpy.ops.wm.obj_export(filepath=export_path, export_selected_objects=True)
        elif args.format == 'fbx':
            bpy.ops.export_scene.fbx(filepath=export_path, use_selection=True)
        elif args.format == 'glb':
            # For GLB, ensure textures are included
            print("Using GLB format")
            # Explicit parameters to be safe
            bpy.ops.export_scene.gltf(
                filepath=export_path, 
                export_format='GLB', 
                use_selection=True,
                export_image_format='AUTO'
            )
        
        # Verify file creation
        if os.path.exists(export_path):
            print(f"File successfully created: {export_path}")
            size_mb = os.path.getsize(export_path) / (1024 * 1024)
            print(f"Output size: {size_mb:.2f} MB")
        else:
            print(f"ERROR: Export operator completed but no file found at: {export_path}")
            
    except Exception as e:
        print(f"ERROR during export: {str(e)}")
        import traceback
        traceback.print_exc()
        
    # Cleanup temp dir and ZIP results
    print("Finalizing results..."); sys.stdout.flush()
    
    if args.format == 'obj':
        # Collect all files to zip from bake_dir
        files_to_zip = []
        for f in os.listdir(bake_dir):
            # Include OBJ, MTL, and texture PNGs
            if f.endswith(('.obj', '.mtl', '.png')):
                files_to_zip.append(os.path.join(bake_dir, f))
                    
        if files_to_zip:
            # Ensure output ends with .zip if we are zipping
            zip_path = args.output
            if not zip_path.lower().endswith('.zip'):
                zip_path = os.path.splitext(args.output)[0] + ".zip"
                
            print(f"Creating ZIP package: {zip_path}"); sys.stdout.flush()
            try:
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for f_path in files_to_zip:
                        zipf.write(f_path, os.path.basename(f_path))
                print(f"ZIP package created successfully: {os.path.basename(zip_path)}"); sys.stdout.flush()
            except Exception as e:
                print(f"Error creating ZIP package: {e}"); sys.stdout.flush()

    # Always cleanup the temporary bake directory
    if 'bake_dir_obj' in locals():
        print(f"Cleaning up temporary directory: {bake_dir}"); sys.stdout.flush()
        bake_dir_obj.cleanup()
        
    print("Processing complete!"); sys.stdout.flush()

if __name__ == "__main__":
    main()
