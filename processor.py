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
            
        found_attributes = set()
        modified = False
        def clean_recursive(obj, key=None):
            nonlocal modified
            if isinstance(obj, dict):
                to_remove = []
                for k in list(obj.keys()):
                    if key == 'attributes':
                        found_attributes.add(k)
                    
                    # CRITICAL: Preserve _SURFACE_PROPS and COLOR_0
                    # Handle variants like __SURFACE_PROPS caused by Blender join
                    if k.startswith('_') and 'SURFACE_PROPS' not in k.upper():
                        to_remove.append(k)
                    elif k in ['extras', 'animations', 'skins', 'cameras']:
                        # Keep extras if it might contain MiniPainterDecal, but strip for stability
                        # Actually, keeping extras for objects might be useful for decals.
                        # For now, stay safe and only keep what we know we need.
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
        print(f"Found attributes in GLB: {', '.join(sorted(list(found_attributes)))}")
        
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
            
            # --- Cleanup High Poly ---
            print("Cleaning up HighPoly (Remove Doubles)...")
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.remove_doubles(threshold=0.0001)
            bpy.ops.object.mode_set(mode='OBJECT')
            
            bpy.context.view_layer.update()
        else:
            high_poly = imported_objs[0]
            
        high_poly.name = "HighPoly"
        
        # Clear custom normals (often imported from GLTF and block smooth shading)
        if high_poly.data.has_custom_normals:
            print("Clearing custom split normals from HighPoly...")
            bpy.ops.object.select_all(action='DESELECT')
            high_poly.select_set(True)
            bpy.context.view_layer.objects.active = high_poly
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
            bpy.ops.object.mode_set(mode='OBJECT')
        
        # Apply smooth shading to high poly
        print("Applying smooth shading to HighPoly...")
        bpy.ops.object.select_all(action='DESELECT')
        high_poly.select_set(True)
        bpy.context.view_layer.objects.active = high_poly
        bpy.ops.object.shade_smooth()
        
        # --- Audit High Poly ---
        print(f"HighPoly Audit:")
        print(f" - Vertices: {len(high_poly.data.vertices)}")
        print(f" - Polygons: {len(high_poly.data.polygons)}")
        print(f" - UV Layers: {', '.join([uv.name for uv in high_poly.data.uv_layers])}")
        
        # Log ALL attributes for debugging
        all_attrs = [a.name for a in high_poly.data.attributes]
        print(f" - All Attributes: {', '.join(all_attrs)}")
        
        color_attrs = [col.name for col in high_poly.data.color_attributes]
        print(f" - Color Attributes: {', '.join(color_attrs)}")
        print(f" - Materials: {len(high_poly.data.materials)}")
        
        # Find the best attribute for color
        target_col_attr = None
        # Preferred names: prioritize COLOR_0 per specification
        for name in ['COLOR_0', 'Color', 'Col', 'color']:
            if name in color_attrs:
                target_col_attr = name
                break
        
        # If not found, take the first one that isn't 'position'
        if not target_col_attr:
            for name in color_attrs:
                if name.lower() not in ['position']:
                    target_col_attr = name
                    break
        
        if target_col_attr:
            print(f"Selected color attribute for baking: '{target_col_attr}'")
        
        # Robust check for SURFACE_PROPS variants
        surface_props_attr = None
        for attr in high_poly.data.attributes:
            if 'SURFACE_PROPS' in attr.name.upper():
                surface_props_attr = attr.name
                # Keep searching for the one with more underscores if multiple exist
                # as Blender join tends to add them.
        
        if surface_props_attr:
            print(f"Found surface properties attribute on high poly: {surface_props_attr}")
        
        # Ensure high poly has at least one material for baking (albedo/color)
        if not high_poly.data.materials:
            mat = bpy.data.materials.new(name="HighPolyDefault")
            high_poly.data.materials.append(mat)
            print("Created fallback material for HighPoly.")
        
        # --- Setup HighPoly Material for Baking ---
        for mat in high_poly.data.materials:
            if mat is None: continue
            mat.use_nodes = True
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            
            # Find Principled BSDF
            principled = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if not principled:
                principled = nodes.new('ShaderNodeBsdfPrincipled')
                output = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)
                if not output:
                    output = nodes.new('ShaderNodeOutputMaterial')
                links.new(principled.outputs['BSDF'], output.inputs['Surface'])
            
            # Link Color
            if target_col_attr:
                attr_node = next((n for n in nodes if n.type == 'ATTRIBUTE' and n.attribute_name == target_col_attr), None)
                if not attr_node:
                    attr_node = nodes.new('ShaderNodeAttribute')
                    attr_node.attribute_name = target_col_attr
                    attr_node.location = (principled.location.x - 300, principled.location.y)
                links.new(attr_node.outputs['Color'], principled.inputs['Base Color'])

            # Link Surface Properties (Roughness, Metallic, Sheen)
            if surface_props_attr:
                # Use a specific attribute node for surface props
                sp_node = next((n for n in nodes if n.type == 'ATTRIBUTE' and n.attribute_name == surface_props_attr), None)
                if not sp_node:
                    sp_node = nodes.new('ShaderNodeAttribute')
                    sp_node.attribute_name = surface_props_attr
                    sp_node.location = (principled.location.x - 600, principled.location.y - 300)
                
                # Separate RGB/XYZ (X=Rough, Y=Metal, Z=Sheen)
                sep_node = nodes.new('ShaderNodeSeparateColor')
                sep_node.location = (sp_node.location.x + 200, sp_node.location.y)
                links.new(sp_node.outputs['Color'], sep_node.inputs['Color'])
                
                links.new(sep_node.outputs['Red'], principled.inputs['Roughness'])
                links.new(sep_node.outputs['Green'], principled.inputs['Metallic'])
                
                # Sheen Weight in Blender 4.2+ is Principled BSDF -> Sheen -> Weight
                # In some versions it might be separate. We'll try to find 'Sheen Weight'
                sheen_input = principled.inputs.get('Sheen Weight')
                if not sheen_input:
                    # Fallback for older Principled BSDF
                    sheen_input = principled.inputs.get('Sheen')
                
                if sheen_input:
                    links.new(sep_node.outputs['Blue'], sheen_input)
            
            # Fallback PBR values
            if not surface_props_attr:
                if principled.inputs['Roughness'].is_linked == False:
                    principled.inputs['Roughness'].default_value = 1.0
                if principled.inputs['Metallic'].is_linked == False:
                    principled.inputs['Metallic'].default_value = 0.0
        
        return high_poly, surface_props_attr
        
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
    
    # Aggressively clean low poly but PRESERVE _SURFACE_PROPS
    for uv in list(low_poly.data.uv_layers):
        print(f" - Removing existing UV layer: {uv.name}")
        low_poly.data.uv_layers.remove(uv)
    
    for col in list(low_poly.data.color_attributes):
        if 'SURFACE_PROPS' in col.name.upper():
            print(f" - PRESERVING Color Attribute: {col.name}")
            continue
        print(f" - Removing existing Color Attribute: {col.name}")
        low_poly.data.color_attributes.remove(col)
    
    # Check regular attributes too
    for attr in list(low_poly.data.attributes):
        if 'SURFACE_PROPS' in attr.name.upper() or attr.name == 'position': continue
        # Most other custom attributes should be removed to avoid issues
        print(f" - Removing existing Attribute: {attr.name}")
        # Blender might not let us remove certain built-in attributes
        try:
            low_poly.data.attributes.remove(attr)
        except: pass
    
    bpy.context.view_layer.update()
    print("Low poly cleaned."); sys.stdout.flush()
    
    # Decimate
    decimate_mesh(low_poly, target_triangles)
    
    # --- Cleanup and Validate Low Poly ---
    print("Cleaning up LowPoly...")
    bpy.context.view_layer.objects.active = low_poly
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    # Remove degenerate/zero-area elements that can break normal mapping/export
    bpy.ops.mesh.dissolve_degenerate(threshold=0.0001)
    # Ensure no doubled vertices post-decimation (sometimes happens with complex modifiers)
    bpy.ops.mesh.remove_doubles(threshold=0.0001)
    bpy.ops.object.mode_set(mode='OBJECT')
    
    # Clear custom normals (CRITICAL for normal maps on decimated meshes)
    if low_poly.data.has_custom_normals:
        print("Clearing custom split normals from LowPoly...")
        bpy.ops.object.select_all(action='DESELECT')
        low_poly.select_set(True)
        bpy.context.view_layer.objects.active = low_poly
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
        bpy.ops.object.mode_set(mode='OBJECT')

    # Apply smooth shading to low poly (CRITICAL for normal maps)
    print("Applying smooth shading to LowPoly...")
    bpy.ops.object.select_all(action='DESELECT')
    low_poly.select_set(True)
    bpy.context.view_layer.objects.active = low_poly
    bpy.ops.object.shade_smooth()
    
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
    
    return mat, bake_tex

def pack_orm_textures(ao_path, rough_path, metal_path, output_path):
    """
    Packs AO, Roughness, and Metallic textures into a single ORM map.
    Red: AO (Default 1.0), Green: Roughness, Blue: Metalness
    """
    print(f"Packing ORM texture to {output_path}...")
    
    try:
        import numpy as np
        # We'll use Blender's internal image handling to load and numpy for packing
        
        def load_image_to_numpy(path):
            if not os.path.exists(path):
                return None
            img = bpy.data.images.load(path)
            # pixels is a flat float array (RGBA)
            pixels = np.array(img.pixels[:])
            width, height = img.size
            # Reshape to (H, W, 4)
            pixels = pixels.reshape((height, width, 4))
            bpy.data.images.remove(img)
            return pixels, width, height

        ao_data = load_image_to_numpy(ao_path) if ao_path else None
        rough_data = load_image_to_numpy(rough_path) if rough_path else None
        metal_data = load_image_to_numpy(metal_path) if metal_path else None
        
        # Use dimensions from any available map
        for data in [ao_data, rough_data, metal_data]:
            if data:
                pixels, w, h = data
                break
        else:
            print("No ORM components found to pack.")
            return False
            
        # Create ORM array (H, W, 4) - RGB + Alpha
        orm_pixels = np.zeros((h, w, 4), dtype=np.float32)
        orm_pixels[:, :, 3] = 1.0 # Alpha
        
        # Red Channel: AO (Default 1.0)
        if ao_data:
            orm_pixels[:, :, 0] = ao_data[0][:, :, 0]
        else:
            orm_pixels[:, :, 0] = 1.0
            
        # Green Channel: Roughness
        if rough_data:
            orm_pixels[:, :, 1] = rough_data[0][:, :, 0] # Assume grayscale, take R channel
        else:
            orm_pixels[:, :, 1] = 0.5 # Fallback
            
        # Blue Channel: Metalness
        if metal_data:
            orm_pixels[:, :, 2] = metal_data[0][:, :, 0] # Assume grayscale, take R channel
        else:
            orm_pixels[:, :, 2] = 0.0 # Fallback
            
        # Save ORM image
        orm_img = bpy.data.images.new("ORM_Pack", width=w, height=h)
        orm_img.pixels = orm_pixels.flatten().tolist()
        orm_img.filepath_raw = output_path
        orm_img.file_format = 'PNG'
        orm_img.save()
        bpy.data.images.remove(orm_img)
        print("ORM packing successful.")
        return True
        
    except Exception as e:
        print(f"Error during ORM packing: {e}")
        import traceback
        traceback.print_exc()
        return False

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
    bpy.context.scene.render.bake.cage_extrusion = 0.3 # Increased for complex minis
    bpy.context.scene.render.bake.use_clear = True
    bpy.context.scene.render.bake.use_clear = True
    
    # Configure pass settings for Diffuse to avoid black shadows
    if bake_type == 'DIFFUSE':
        bpy.context.scene.render.bake.use_pass_direct = False
        bpy.context.scene.render.bake.use_pass_indirect = False
        bpy.context.scene.render.bake.use_pass_color = True
    
    # Perform bake
    print(f"Starting bake operator for {bake_type}...")
    try:
        bpy.ops.object.bake(type=bake_type)
        print(f"Bake operator for {bake_type} finished.")
    except Exception as e:
        print(f"ERROR: Bake operator failed for {bake_type}: {e}")
        return None
    
    # Save image
    img.filepath_raw = output_path
    img.file_format = 'PNG'
    img.save()
    print(f"Saved {bake_type} bake to {output_path}")
    
    return img

def apply_baked_textures(low_poly, diffuse_img=None, normal_img=None, orm_img=None, sheen_img=None):
    """
    Connects the baked textures to the material of the low poly model for preview/export.
    """
    if not low_poly.data.materials:
        return
        
    mat = low_poly.data.materials[0]
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    principled = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    
    if not principled:
        return
    
    if diffuse_img:
        diff_node = nodes.new('ShaderNodeTexImage')
        diff_node.image = diffuse_img
        links.new(diff_node.outputs['Color'], principled.inputs['Base Color'])
        
    if normal_img:
        norm_node = nodes.new('ShaderNodeTexImage')
        norm_node.image = normal_img
        norm_node.image.colorspace_settings.name = 'Non-Color'
        
        norm_map = nodes.new('ShaderNodeNormalMap')
        links.new(norm_node.outputs['Color'], norm_map.inputs['Color'])
        links.new(norm_map.outputs['Normal'], principled.inputs['Normal'])

    if orm_img:
        orm_node = nodes.new('ShaderNodeTexImage')
        orm_node.image = orm_img
        orm_node.image.colorspace_settings.name = 'Non-Color'
        orm_node.location = (principled.location.x - 600, principled.location.y - 300)
        
        sep = nodes.new('ShaderNodeSeparateColor')
        links.new(orm_node.outputs['Color'], sep.inputs['Color'])
        links.new(sep.outputs['Green'], principled.inputs['Roughness'])
        links.new(sep.outputs['Blue'], principled.inputs['Metallic'])

    if sheen_img:
        sheen_node = nodes.new('ShaderNodeTexImage')
        sheen_node.image = sheen_img
        sheen_node.image.colorspace_settings.name = 'Non-Color'
        sheen_node.location = (principled.location.x - 600, principled.location.y + 300)
        
        sheen_input = principled.inputs.get('Sheen Weight') or principled.inputs.get('Sheen')
        if sheen_input:
            links.new(sheen_node.outputs['Color'], sheen_input)

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
    high_poly, surface_props_attr = import_model(args.input)
    print("Import model returned."); sys.stdout.flush()
    
    # Check for UVs on high poly to handle "Mesh Shredding" fallback
    has_uvs = any(uv for uv in high_poly.data.uv_layers)
    if not has_uvs:
        print("Model has NO UVs. Logic for 'Mesh Shredding' could be applied here if needed.")
        # Currently we generate UVs during prepare_low_poly, which is standard for baking.
    
    print(f"Decimating to {args.triangles} triangles..."); sys.stdout.flush()
    low_poly = prepare_low_poly(high_poly, args.triangles)
    
    # Preserve Custom User Data (e.g. MiniPainterDecal)
    if 'MiniPainterDecal' in high_poly.name:
        low_poly.name = high_poly.name # Keep specific name if it identifies it
    
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
    rough_img = None
    metal_img = None
    ao_img = None
    orm_img = None
    sheen_img = None
    
    if args.bake_diffuse:
        diff_path = os.path.join(bake_dir, "diffuse.png")
        print("Baking Base Color (Diffuse) pass...")
        
        # Use EMIT trick for accurate Base Color bake (ignores lighting and metallic attenuation)
        for mat in high_poly.data.materials:
            if not mat or not mat.use_nodes: continue
            principled = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if principled:
                # Find the color attribute node we created in import_model
                color_node = next((n for n in mat.node_tree.nodes if n.type == 'ATTRIBUTE' and 'SURFACE_PROPS' not in n.attribute_name.upper()), None)
                if color_node:
                    mat.node_tree.links.new(color_node.outputs['Color'], principled.inputs['Emission Color'])
                
                if principled.inputs.get('Emission Strength'):
                    principled.inputs['Emission Strength'].default_value = 1.0
        
        diff_img = bake_and_save(high_poly, low_poly, 'EMIT', "DiffuseBake", args.resolution, args.resolution, diff_path)
        
        # Cleanup
        for mat in high_poly.data.materials:
            if not mat or not mat.use_nodes: continue
            principled = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if principled:
                link = principled.inputs['Emission Color'].links[0] if principled.inputs['Emission Color'].links else None
                if link: mat.node_tree.links.remove(link)
                if principled.inputs.get('Emission Strength'): principled.inputs['Emission Strength'].default_value = 0.0
        
    if args.bake_normal:
        norm_path = os.path.join(bake_dir, "normal.png")
        norm_img = bake_and_save(high_poly, low_poly, 'NORMAL', "NormalBake", args.resolution, args.resolution, norm_path)
        
    if args.bake_roughness:
        rough_path = os.path.join(bake_dir, "roughness.png")
        print("Baking Roughness pass...")
        for mat in high_poly.data.materials:
            if not mat or not mat.use_nodes: continue
            principled = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if principled:
                if surface_props_attr:
                    sep_node = next((n for n in mat.node_tree.nodes if n.type == 'SEPARATE_COLOR'), None)
                    if sep_node:
                        mat.node_tree.links.new(sep_node.outputs['Red'], principled.inputs['Emission Color'])
                else:
                    mat.node_tree.links.new(principled.inputs['Roughness'], principled.inputs['Emission Color'])
                
                if principled.inputs.get('Emission Strength'):
                    principled.inputs['Emission Strength'].default_value = 1.0
                    
        rough_img = bake_and_save(high_poly, low_poly, 'EMIT', "RoughnessBake", args.resolution, args.resolution, rough_path)
        
        # Cleanup
        for mat in high_poly.data.materials:
            if not mat or not mat.use_nodes: continue
            principled = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if principled:
                link = principled.inputs['Emission Color'].links[0] if principled.inputs['Emission Color'].links else None
                if link: mat.node_tree.links.remove(link)
                if principled.inputs.get('Emission Strength'): principled.inputs['Emission Strength'].default_value = 0.0
        
    if args.bake_metallic:
        print("Baking Metallic pass...")
        metal_path = os.path.join(bake_dir, "metallic.png")
        # Use EMIT trick for accurate Metallic parameter bake
        for mat in high_poly.data.materials:
            if not mat or not mat.use_nodes: continue
            principled = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if principled:
                if surface_props_attr:
                    # Link attribute directly for best accuracy
                    sep_node = next((n for n in mat.node_tree.nodes if n.type == 'SEPARATE_COLOR'), None)
                    if sep_node:
                        mat.node_tree.links.new(sep_node.outputs['Green'], principled.inputs['Emission Color'])
                else:
                    # Link the parameter itself
                    mat.node_tree.links.new(principled.inputs['Metallic'], principled.inputs['Emission Color'])
                
                if principled.inputs.get('Emission Strength'):
                    principled.inputs['Emission Strength'].default_value = 1.0
                    
        metal_img = bake_and_save(high_poly, low_poly, 'EMIT', "MetallicBake", args.resolution, args.resolution, metal_path)
        
        # Cleanup
        for mat in high_poly.data.materials:
            if not mat or not mat.use_nodes: continue
            principled = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if principled:
                link = principled.inputs['Emission Color'].links[0] if principled.inputs['Emission Color'].links else None
                if link: mat.node_tree.links.remove(link)
                if principled.inputs.get('Emission Strength'): principled.inputs['Emission Strength'].default_value = 0.0

    # Bake Sheen if the attribute was found
    if surface_props_attr and args.format == 'glb':
        print("Baking Sheen pass...")
        sheen_path = os.path.join(bake_dir, "sheen.png")
        
        for mat in high_poly.data.materials:
            if not mat or not mat.use_nodes: continue
            principled = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            sep_node = next((n for n in mat.node_tree.nodes if n.type == 'SEPARATE_COLOR'), None)
            if principled and sep_node:
                mat.node_tree.links.new(sep_node.outputs['Blue'], principled.inputs['Emission Color'])
                if principled.inputs.get('Emission Strength'):
                    principled.inputs['Emission Strength'].default_value = 1.0
        
        sheen_img = bake_and_save(high_poly, low_poly, 'EMIT', "SheenBake", args.resolution, args.resolution, sheen_path)
        
        # Cleanup
        for mat in high_poly.data.materials:
            if not mat or not mat.use_nodes: continue
            principled = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if principled:
                link = principled.inputs['Emission Color'].links[0] if principled.inputs['Emission Color'].links else None
                if link: mat.node_tree.links.remove(link)
                if principled.inputs.get('Emission Strength'): principled.inputs['Emission Strength'].default_value = 0.0

    # For GLB format, we pack to ORM
    if args.format == 'glb' and (args.bake_roughness or args.bake_metallic):
        # We might want AO even if not requested specifically for a better ORM
        ao_path = os.path.join(bake_dir, "ao.png")
        ao_img = bake_and_save(high_poly, low_poly, 'AO', "AOBake", args.resolution, args.resolution, ao_path)
        
        orm_path = os.path.join(bake_dir, "orm.png")
        if pack_orm_textures(ao_path if ao_img else None, 
                             rough_path if rough_img else None, 
                             metal_path if metal_img else None, 
                             orm_path):
            orm_img = bpy.data.images.load(orm_path)
            
    # Apply textures for formats that pack them (GLB) or for visual correctness
    apply_baked_textures(low_poly, diff_img, norm_img, orm_img, sheen_img)
    
    # Handle Sheen if _SURFACE_PROPS.z is present (Industry standard for Mini Painter Studio)
    # This is a bit advanced for a simple script, but we can at least ensure 
    # the attribute is present on the exported mesh since we preserved it.
    
    # Hide high poly
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
            # For GLB, ensure textures are included and custom attributes are exported
            print("Using GLB format with custom attribute export")
            bpy.ops.export_scene.gltf(
                filepath=export_path, 
                export_format='GLB', 
                use_selection=True,
                export_image_format='AUTO',
                export_attributes=True, # Ensure _SURFACE_PROPS is exported
                export_extras=True
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
            if f.endswith(('.obj', '.mtl', '.png')):
                files_to_zip.append(os.path.join(bake_dir, f))
                    
        if files_to_zip:
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
